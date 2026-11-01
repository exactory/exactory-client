"""Epoch limits consume durable resources in the same transaction as admission."""

import copy

from research_harness import review_task_execution as execution
from research_harness import review_staged_followups as followups
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

    def test_reread_fragments_share_one_round_and_questions_have_separate_caps(self):
        self.budgets(max_rereads=1)
        self.reread('a', 'retrieval:one:reread:1')
        self.reread('b', 'retrieval:one:reread:1')
        self.reread('c', 'retrieval:two:reread:1')
        self.reserve('a')
        self.reserve('b')
        self.reserve('c')
        self.assertEqual(self.ledger()['rereads'], 2)
        self.assertEqual(len(self.store.snapshot()['records']['review_reservation']), 3)

    def test_each_task_has_its_own_retry_and_aggregate_usage_survives_reopening(self):
        self.budgets(max_retries=1)
        self.end(self.reserve('a')['result'])
        self.end(self.reserve('a', request_id='retry-a')['result'])
        self.end(self.reserve('b')['result'])
        self.store = Store(self.root)
        self.end(self.reserve('b', request_id='retry-b')['result'])
        self.assertEqual((self.ledger()['attempts'], self.ledger()['retries']), (4, 2))
        self.refuse('review_task_exhausted', lambda: self.reserve('b', request_id='third-b'))

    def test_two_rereads_of_one_question_do_not_exhaust_a_different_question(self):
        self.budgets(max_rereads=2, max_calls=3)
        for task, obligation in (('a', 'retrieval:q1:reread:1'),
                                 ('b', 'retrieval:q1:reread:2'),
                                 ('c', 'retrieval:q2:reread:1')):
            self.reread(task, obligation)
            self.end(self.reserve(task)['result'])
        self.assertEqual(self.ledger()['rereads'], 3)
        self.put('review_task', 'd', dict(id='d', epoch_id='epoch', generation=1))
        self.refuse('assessment_incomplete', lambda: self.reserve('d'))
        self.assertEqual(self.ledger()['attempts'], 3)

    def test_reread_limit_survives_reopening_and_a_new_task_identity(self):
        self.budgets(max_rereads=2)
        for task, round_number in (('a', 1), ('b', 2)):
            self.reread(task, 'retrieval:q1:reread:' + str(round_number))
            self.end(self.reserve(task)['result'])
        self.store = Store(self.root)
        self.reread('c', 'retrieval:q1:reread:3')
        self.refuse('assessment_incomplete', lambda: self.reserve('c'))
        self.assertEqual(self.ledger()['rereads'], 2)

    def test_source_and_dependency_versions_receive_separate_question_allowances(self):
        from test_review_staged_followups import RetrievalFollowupTests
        fixture = RetrievalFollowupTests()
        fixture.setUp()
        self.budgets(max_rereads=2)
        original = followups._derive_obligation(fixture.value, fixture.pending, fixture.value['manifest']['contexts'])
        changed_source = copy.deepcopy(fixture.value)
        source_id = fixture.pending['inputs'][0]['source_id']
        next(row for row in changed_source['manifest']['sources'] if row['id'] == source_id)['identity'] = {'version': 'new'}
        source_version = followups._derive_obligation(changed_source, fixture.pending, changed_source['manifest']['contexts'])
        contexts = copy.deepcopy(fixture.value['manifest']['contexts'])
        contexts[fixture.pending['claim_ids'][0]]['root'] = 'new-dependency-root'
        dependency_version = followups._derive_obligation(fixture.value, fixture.pending, contexts)
        for task_id, obligation, round_number in (('a', original, 1), ('b', original, 2),
                                                   ('c', source_version, 1), ('d', dependency_version, 1)):
            self.reread(task_id, obligation + ':reread:' + str(round_number))
            self.end(self.reserve(task_id)['result'])
        self.assertEqual(self.ledger()['rereads'], 4)

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
        self.reread('b', 'retrieval:one:reread:2')
        self.reserve('a')
        self.reserve('b')
        epoch = self.store.snapshot()['records']['review_epoch']['epoch']
        self.put('review_epoch', 'epoch', dict(epoch, budgets=dict(max_rereads=1)))
        self.refuse('assessment_incomplete', lambda: self.reserve('c'))
        self.assertEqual(self.ledger()['rereads'], 2)
