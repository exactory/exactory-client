"""Keyed polling stays advisory and performs no authoritative history replay."""

import sqlite3
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import storage
from research_harness.errors import ResearchError


class AdvisoryRecordReadTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.store.mutate('fixture', {}, lambda tx: (
            tx.put('review_task_execution', 'own', {'state': 'running'}),
            tx.put('review_task_execution', 'foreign', {'state': 'reserved'}),
            tx.put('review_pool_membership', 'own', {'released': False})),
            expected_revision=0, request_id='fixture')

    def poll(self, keys):
        read = getattr(self.store, 'read_records', None)
        self.assertTrue(callable(read), 'Keyed advisory polling must read only the requested record rows')
        return read(keys)

    def test_poll_reads_requested_rows_only_without_replaying_events_or_receipts(self):
        statements = []
        original = self.store._open

        def trace(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.store, '_open', side_effect=trace), \
                patch.object(storage, '_read_snapshot', side_effect=AssertionError('No authoritative snapshot')):
            revision, event_digest, records = self.poll([
                ('review_task_execution', 'own'), ('review_pool_membership', 'own'), ('missing', 'key')])
        self.assertEqual(revision, 1)
        self.assertEqual(len(event_digest), 64)
        self.assertEqual(records, {'review_task_execution': {'own': {'state': 'running'}},
                                  'review_pool_membership': {'own': {'released': False}}, 'missing': {}})
        reads = [query for query in statements if 'FROM records' in query]
        self.assertEqual(len(reads), 3, reads)
        self.assertTrue(all('kind =' in query and 'key =' in query for query in reads), reads)
        self.assertFalse(any('FROM receipts' in query or 'SELECT changes' in query or 'SELECT * FROM events' in query
                             for query in statements), statements)
        records['review_task_execution']['own'].clear()
        self.assertEqual(self.poll([('review_task_execution', 'own')])[2],
                         {'review_task_execution': {'own': {'state': 'running'}}})

    def test_empty_poll_is_consistent_and_invalid_keys_are_refused(self):
        self.assertEqual(self.poll([])[2], {})
        for keys in ([('', 'id')], [('kind', '')], [('kind',)], ['kind'], [('kind', 3)]):
            with self.subTest(keys=keys), self.assertRaises(ResearchError) as raised:
                self.poll(keys)
            self.assertEqual(raised.exception.code, 'invalid_input')

    def test_unrequested_corruption_can_be_polled_but_never_authorizes_a_commit(self):
        self.poll([('review_task_execution', 'own')])
        with sqlite3.connect(str(self.store._path)) as connection:
            connection.execute("UPDATE records SET value = 'invalid-json' WHERE key = 'foreign'")
        advisory = self.poll([('review_task_execution', 'own')])
        self.assertEqual(advisory[2]['review_task_execution']['own']['state'], 'running')
        with self.assertRaises(ResearchError) as raised:
            self.store.snapshot()
        self.assertEqual(raised.exception.code, 'corrupt_state')

    def test_advisory_rows_are_not_a_scoped_preparation_proof(self):
        advisory = self.poll([('review_pool_membership', 'own')])
        with self.assertRaises(ResearchError) as raised:
            self.store.mutate_scoped('release', {}, lambda tx, revision: tx.put(
                'review_pool_membership', 'own', {'released': True}),
                request_id='release', _preparation=advisory)
        self.assertEqual(raised.exception.code, 'invalid_preparation')
        self.assertFalse(self.store.snapshot()['records']['review_pool_membership']['own']['released'])
