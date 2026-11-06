"""Authenticated post creation chronology without retaining problem history."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.storage import Store, select_record_groups


class PostCreationRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'problem'
        self.store = Store(self.root, create=True)
        self.put('problem_statement', 'statement', {'statement': 'Synthetic claim'})
        self.post = {'id': 'post', 'problem_id': 'synthetic-problem', 'statement_id': 'statement',
                     'statement_digest': 'synthetic-statement', 'problem_evidence_digest': 'synthetic-evidence',
                     'export_digest': 'synthetic-export',
                     'posted_at': '2040-01-01T00:00:00Z'}
        self.response = self.put('problem_post', 'post', self.post)

    def put(self, kind, key, value):
        revision = self.store.revision
        return self.store.mutate('fixture', {'kind': kind, 'key': key, 'value': value},
                                 lambda tx: tx.put(kind, key, value), expected_revision=revision,
                                 request_id='fixture-' + str(revision))

    def assert_creation(self, records, expected=2):
        self.assertTrue(callable(getattr(records, 'read_creation_revision', None)),
                        'Authenticated records must expose post creation revisions')
        self.assertEqual(records.read_creation_revision('problem_post', 'post'), expected)
        self.assertIsNone(records.read_creation_revision('problem_post', 'missing'))
        self.assertIsNone(records.read_creation_revision('problem_statement', 'statement'))

    def test_legacy_post_uses_earliest_event_and_ignores_later_writes(self):
        original = self.store.snapshot()['records']
        self.assert_creation(original)
        self.put('problem_post', 'post', dict(self.post, posted_at='2041-01-01T00:00:00Z'))
        self.put('problem_post', 'later', dict(self.post, id='later'))
        current = self.store.snapshot()['records']
        self.assert_creation(current)
        self.assertEqual(current.read_creation_revision('problem_post', 'later'), 4)
        self.assertIsNone(original.read_creation_revision('problem_post', 'later'))
        self.assertNotIn('recorded_revision', original['problem_post']['post'])

    def test_problem_snapshots_and_copies_retain_only_post_creation_metadata(self):
        with self.store.guarded_snapshot() as guard:
            records = guard.snapshot()['records']
        for view in (records, records.copy(), copy.copy(records), copy.deepcopy(records)):
            with self.subTest(view_type=type(view).__name__):
                self.assert_creation(view)
                self.assertFalse(hasattr(view, '_history'))
                self.assertFalse(hasattr(view, 'read_snapshot'))
        records['problem_post']['post']['id'] = 'caller edit'
        self.assert_creation(records)
        self.assertEqual(self.store.snapshot()['records']['problem_post']['post'], self.post)

    def test_full_preparations_and_copies_preserve_creation_metadata(self):
        records = self.store._read_preparation().records
        for view in (records, records.copy(), copy.copy(records), copy.deepcopy(records)):
            self.assert_creation(view)
            self.assertFalse(hasattr(view, 'read_snapshot'))
        records['problem_post']['post']['id'] = 'caller edit'
        self.assertEqual(records['problem_post']['post'], self.post)

    def test_managed_and_historical_snapshots_bound_creation_revision(self):
        self.put('configuration', 'research', {'profile': 'research'})
        self.put('workspace', 'study', {'stage': 'literature'})
        records = self.store.snapshot()['records']
        self.assert_creation(records)
        for revision, expected in ((0, None), (1, None), (2, 2), (4, 2)):
            historical = records.read_snapshot(revision)
            for view in (historical, historical.copy(), copy.copy(historical), copy.deepcopy(historical)):
                with self.subTest(revision=revision, view_type=type(view).__name__):
                    self.assert_creation(view, expected)
        prepared = self.store._read_preparation().records
        self.assert_creation(prepared.copy())
        self.assert_creation(prepared.read_snapshot(2))

    def test_scoped_views_and_copies_preserve_creation_metadata(self):
        factories = (lambda records: records,
                     lambda records: select_record_groups(records, {'review_assignment': ({'role': 'bar'},)}),
                     lambda records: records.copy(), copy.copy, copy.deepcopy)
        for factory in factories:
            with self.subTest(factory=factory):
                preparation = self.store._read_scoped_preparation()
                self.assert_creation(factory(preparation.records))
                proof = preparation.release_proof()
                self.assertIn(('problem_post', 'post'), {(kind, key) for kind, key, value in proof.reads})

    def test_scoped_creation_lookup_binds_only_selected_key(self):
        preparation = self.store._read_scoped_preparation()
        self.assert_creation(preparation.records)
        proof = preparation.release_proof()
        self.assertFalse(proof.global_revision)
        self.put('unrelated', 'later', {})
        result = self.store.mutate_scoped('accept', {}, lambda tx, revision: {'ok': True},
                                         request_id='accept', _preparation=proof)
        self.assertTrue(result['result']['ok'])

    def test_scoped_creation_lookup_rejects_changed_or_newly_created_key(self):
        for key in ('post', 'missing'):
            with self.subTest(key=key):
                preparation = self.store._read_scoped_preparation()
                self.assertTrue(callable(getattr(preparation.records, 'read_creation_revision', None)))
                preparation.records.read_creation_revision('problem_post', key)
                proof = preparation.release_proof()
                self.put('problem_post', key, dict(self.post, id=key))
                with self.assertRaises(ResearchError) as raised:
                    self.store.mutate_scoped('accept', {}, lambda tx, revision: {},
                                             request_id='accept-' + key, _preparation=proof)
                self.assertEqual(raised.exception.code, 'review_task_dependency_changed')

    def test_scoped_post_membership_rejects_concurrent_publication(self):
        preparation = self.store._read_scoped_preparation()
        records = preparation.records
        for key in records.get('problem_post', {}):
            self.assertTrue(callable(getattr(records, 'read_creation_revision', None)))
            self.assertEqual(records.read_creation_revision('problem_post', key), 2)
        proof = preparation.release_proof()
        self.put('problem_post', 'new-post', dict(self.post, id='new-post'))
        with self.assertRaises(ResearchError) as raised:
            self.store.mutate_scoped('accept', {}, lambda tx, revision: {},
                                     request_id='accept', _preparation=proof)
        self.assertEqual(raised.exception.code, 'review_task_dependency_changed')

    def test_corrupt_history_cannot_supply_creation_metadata(self):
        self.assert_creation(self.store.snapshot()['records'])
        with sqlite3.connect(str(self.store._path)) as connection:
            connection.execute('DROP TRIGGER events_no_update')
            connection.execute("UPDATE events SET digest = 'forged' WHERE revision = 2")
            connection.execute("CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
                               "BEGIN SELECT RAISE(ABORT, 'Research history is append-only'); END")
        for read in (self.store.snapshot, self.store._read_preparation, self.store._read_scoped_preparation):
            with self.subTest(read=read.__name__), self.assertRaises(ResearchError) as raised:
                read()
            self.assertEqual(raised.exception.code, 'corrupt_state')

    def test_transaction_reads_use_authenticated_committed_creation_revisions(self):
        retained = []

        def apply(transaction):
            self.assertTrue(callable(getattr(transaction, 'read_creation_revision', None)))
            retained.append(transaction)
            self.assertEqual(transaction.read_creation_revision('problem_post', 'post'), 2)
            transaction.put('problem_post', 'post', dict(self.post, id='updated'))
            transaction.put('problem_post', 'pending', dict(self.post, id='pending'))
            self.assertEqual(transaction.read_creation_revision('problem_post', 'post'), 2)
            self.assertIsNone(transaction.read_creation_revision('problem_post', 'pending'))
            return {}

        self.store.mutate('read-creation', {}, apply, expected_revision=2, request_id='read-creation')
        with self.assertRaises(ResearchError) as raised:
            retained[0].read_creation_revision('problem_post', 'post')
        self.assertEqual(raised.exception.code, 'transaction_closed')
        proof = self.store._read_scoped_preparation().release_proof()
        result = self.store.mutate_scoped('read-creation-scoped', {},
            lambda transaction, revision: {'creation': transaction.read_creation_revision('problem_post', 'pending')},
            request_id='read-creation-scoped', _preparation=proof)
        self.assertEqual(result['result'], {'creation': 3})

    def test_creation_reads_preserve_record_event_receipt_and_database_bytes(self):
        before = self.store._path.read_bytes()
        with sqlite3.connect(str(self.store._path)) as connection:
            rows = {table: connection.execute('SELECT * FROM ' + table).fetchall()
                    for table in ('records', 'events', 'receipts', 'metadata')}
        records = self.store.snapshot()['records']
        self.assert_creation(records)
        self.assertEqual(json.loads(json.dumps(records))['problem_post']['post'], self.post)
        self.assertEqual(self.store.committed_request('fixture-1')['response'], self.response)
        self.assertEqual(self.store._path.read_bytes(), before)
        with sqlite3.connect(str(self.store._path)) as connection:
            for table, values in rows.items():
                self.assertEqual(connection.execute('SELECT * FROM ' + table).fetchall(), values)


if __name__ == '__main__':
    unittest.main()
