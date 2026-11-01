"""Epoch limits consume durable resources in the same transaction as admission."""

from research_harness import review_task_execution as execution
from research_harness.storage import Store
from test_review_task_execution import TaskExecutionFixture


class ResourceLedgerTests(TaskExecutionFixture):
    def budgets(self, **limits):
        epoch = self.store.snapshot()['records']['review_epoch']['epoch']
        self.put('review_epoch', 'epoch', dict(epoch, budgets=limits))
        execution.record_capacity(self.store, dict(store_limit=3, route_limits={}), request_id='capacity')

    def end(self, reservation):
        self.put('review_reservation', reservation['id'], dict(reservation, released=True, state='known_failure'))

    def ledger(self):
        return Store(self.root).snapshot()['records'].get('review_resource_ledger', {}).get('epoch')

    def reread(self, name, obligation):
        self.put('review_task', name, dict(id=name, epoch_id='epoch', generation=1,
            task=dict(type='frame', obligation_ids=[obligation])))

    def test_admission_and_replay_consume_one_durable_call(self):
        self.budgets(max_calls=2)
        first = self.reserve()
        self.assertEqual(self.reserve(), first)
        self.assertIsNotNone(self.ledger())
        self.assertEqual(self.ledger()['attempts'], 1)
        self.store = Store(self.root)
        self.reserve('b')
        self.refuse('assessment_incomplete', lambda: self.reserve('c'))
        self.assertEqual(self.ledger()['attempts'], 2)
        exhausted = self.store.snapshot()['records']['review_execution_exhaustion']
        self.assertEqual(len(exhausted), 1)
        self.assertEqual(next(iter(exhausted.values()))['limit'], 'max_calls')

    def test_reread_fragments_share_one_round_but_questions_share_the_epoch_cap(self):
        self.budgets(max_rereads=1)
        self.reread('a', 'retrieval:one:reread:1')
        self.reread('b', 'retrieval:one:reread:1')
        self.reread('c', 'retrieval:two:reread:1')
        self.reserve('a')
        self.reserve('b')
        self.refuse('assessment_incomplete', lambda: self.reserve('c'))
        self.assertEqual(self.ledger()['rereads'], 1)
        self.assertEqual(len(self.store.snapshot()['records']['review_reservation']), 2)

    def test_epoch_retry_limit_cannot_be_reset_by_switching_tasks(self):
        self.budgets(max_retries=1)
        self.end(self.reserve('a')['result'])
        self.end(self.reserve('a', request_id='retry-a')['result'])
        self.end(self.reserve('b')['result'])
        self.refuse('assessment_incomplete', lambda: self.reserve('b', request_id='retry-b'))
        self.assertEqual((self.ledger()['attempts'], self.ledger()['retries']), (3, 1))

    def test_unchanged_scope_cannot_dispatch_beyond_its_successor_epoch_budget(self):
        original = self.store.snapshot()['records']['review_epoch']['epoch']
        self.put('review_epoch', 'epoch', dict(original, slot_id='slot', evidence_currency={'same':True},
                                             budgets=dict(max_epochs=2)))
        for index in range(2):
            self.put('review_epoch', 'old-' + str(index), dict(original, id='old-' + str(index),
                slot_id='slot', evidence_currency={'same':True}))
        self.refuse('assessment_incomplete', lambda: self.reserve())
        self.assertEqual(self.ledger()['epochs'], 3)
        self.assertEqual(self.ledger()['attempts'], 0)

    def test_lowered_reread_budget_does_not_forget_previous_dispatches(self):
        self.budgets(max_rereads=2)
        self.reread('a', 'retrieval:one:reread:1')
        self.reread('b', 'retrieval:two:reread:1')
        self.reserve('a')
        self.reserve('b')
        epoch = self.store.snapshot()['records']['review_epoch']['epoch']
        self.put('review_epoch', 'epoch', dict(epoch, budgets=dict(max_rereads=1)))
        self.refuse('assessment_incomplete', lambda: self.reserve('c'))
        self.assertEqual(self.ledger()['rereads'], 2)
