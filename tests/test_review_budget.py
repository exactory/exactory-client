"""Cumulative review admissions use durable, namespaced call identities."""

import importlib
import importlib.util
import json
from contextlib import contextmanager
from unittest.mock import patch

from research_harness import output_views, review_protocol as protocol
from research_harness import review_task_execution as execution
from research_harness.check_mode import check_mutation
from research_harness.operations import immutable_record
from research_harness.storage import Store
from test_review_task_execution import TaskExecutionFixture
import test_review_task_execution_worker as workers


CALLS = (('review.invoke', 'review_invocation', 'review_attempt'),
         ('review.route_probe', 'review_probe_invocation', 'review_route_probe'),
         ('review.output_correction', 'review_output_correction_invocation', 'review_output_correction'))


class BudgetFixture(TaskExecutionFixture):
    def set_budget(self, maximum):
        self.put('review_budget', 'store', dict(id='store', max_calls=maximum, reason='Test allowance'))

    def read_budget(self):
        report = protocol.usage_report(self.store.snapshot()['records'])
        self.assertIn('review_budget', report, 'Usage must expose the canonical cumulative allowance')
        return report['review_budget']

    def invoke(self, call=CALLS[0], identifier='ordinary', observe=None, route_id='route'):
        operation, kind, completed = call
        payload = dict(id=identifier, route_id=route_id, assignment_id='assignment')

        def start(records, value):
            row = dict(value, request=self.request)
            return [immutable_record(records, kind, identifier, row)], row

        def finish(records, value, invocation, observation, revision):
            row = dict(invocation, **observation, recorded_revision=revision)
            return [immutable_record(records, completed, identifier, row)], row

        with patch.object(protocol, '_observed', side_effect=observe or (
                lambda *args: dict(status='failed', output=None))):
            return protocol._invoke_once(self.store, payload, operation, start, finish,
                expected_revision=self.store.revision, request_id=operation + ':' + identifier, credential=None)


class ReviewBudgetTests(BudgetFixture):
    def test_mixed_calls_consume_one_allowance_each_and_refusal_rolls_back_resources(self):
        self.set_budget(4)
        execution.record_capacity(self.store, dict(store_limit=10, route_limits={}), request_id='capacity')
        for index, call in enumerate(CALLS):
            self.invoke(call, str(index))
        reservation = self.reserve()['result']
        before = self.store.snapshot()
        self.refuse('review_budget_exhausted', lambda: self.reserve('b'))
        self.assertEqual(self.store.snapshot(), before, 'Refusal must roll back prospective epoch resources')
        self.assertEqual(self.read_budget(), dict(configured=True, allowance=4, charged_calls=4, remaining_calls=0))
        self.assertEqual(self.reserve()['result'], reservation, 'Replaying admission must not charge again')

    def test_equal_user_ids_in_three_operation_namespaces_are_three_calls(self):
        self.set_budget(3)
        for call in CALLS:
            self.invoke(call, 'same')
        self.refuse('review_budget_exhausted', lambda: self.invoke(identifier='fourth'))
        self.assertEqual(self.read_budget()['charged_calls'], 3)

    def test_staged_reservation_observation_accepted_attempt_and_replay_are_one_charge(self):
        self.set_budget(1)
        reservation = self.reserve()['result']
        # Historical reconstruction must count the capture and retained attempt
        # by their reservation even when their user-facing attempt IDs differ.
        self.put('review_observation', reservation['id'], dict(reservation_id=reservation['id']))
        self.put('review_attempt', 'staged-attempt', dict(id='staged-attempt', assignment_id='assignment',
            reservation_id=reservation['id'], status='completed'))
        self.put('review_task_acceptance', reservation['id'], dict(reservation_id=reservation['id']))
        self.assertEqual(self.read_budget()['charged_calls'], 1)
        self.assertEqual(self.reserve()['result'], reservation)

    def test_imported_closure_adds_nothing_and_historical_import_adds_one(self):
        self.set_budget(2)
        self.put('review_invocation', 'interrupted', dict(id='interrupted', assignment_id='assignment', route_id='route'))
        self.put('review_attempt', 'interrupted', dict(id='interrupted', assignment_id='assignment',
            status='failed', output=None, origin='imported'))
        self.invoke(identifier='last')
        self.assertEqual(self.read_budget()['charged_calls'], 2)
        self.put('review_attempt', 'historical', dict(id='historical', assignment_id='assignment',
            status='failed', output=None, origin='imported'))
        self.assertEqual(self.read_budget()['charged_calls'], 3)
        self.refuse('review_budget_exhausted', lambda: self.invoke(identifier='blocked'))

    def test_crash_after_ordinary_admission_stays_charged(self):
        self.set_budget(1)
        def crash(*args):
            raise RuntimeError('Stopped after admission before launch')
        with self.assertRaisesRegex(RuntimeError, 'Stopped after admission'):
            self.invoke(observe=crash)
        self.refuse('review_budget_exhausted', lambda: self.invoke(identifier='blocked'))
        self.assertEqual(self.read_budget()['charged_calls'], 1)
        self.assertFalse(self.store.snapshot()['records'].get('review_attempt'))
        result = execution.reconcile_call(self.store, dict(operation='review.invoke', invocation_id='ordinary'),
            request_id='reconcile')['result']
        self.assertEqual(result['state'], 'terminated')
        self.assertEqual(self.read_budget()['charged_calls'], 1)
        self.refuse('review_budget_exhausted', lambda: self.invoke(identifier='still-blocked'))

    def test_responses_calls_are_checked_before_adapter_capacity_exclusion(self):
        self.set_budget(0)
        self.put('review_route', 'responses', dict(id='responses', adapter='openai_responses_v1'))
        self.refuse('review_budget_exhausted', lambda: self.invoke(route_id='responses',
            observe=lambda *args: self.fail('Exhaustion must prevent external I/O')))

    def test_absent_and_explicit_unlimited_allowances_preserve_admission(self):
        for index in range(3):
            self.invoke(identifier=str(index))
        self.assertEqual(self.read_budget(), dict(configured=False, allowance=None, charged_calls=3, remaining_calls=None))
        self.set_budget(None)
        self.invoke(identifier='unlimited')
        self.assertEqual(self.read_budget(), dict(configured=True, allowance=None, charged_calls=4, remaining_calls=None))

    def test_allowance_records_validate_replay_revision_lowering_and_raising(self):
        self.assertIsNotNone(importlib.util.find_spec('research_harness.review_budget'),
                             'An allowance needs a domain recording function')
        budget = importlib.import_module('research_harness.review_budget')
        payload = dict(max_calls=1, reason='One call')
        revision = self.store.revision
        first = budget.record_budget(self.store, payload, expected_revision=revision, request_id='budget')
        self.assertEqual(first['result']['id'], 'store')
        self.invoke()
        self.assertEqual(budget.record_budget(self.store, payload, expected_revision=revision, request_id='budget'), first)
        for maximum in (-1, True, 1.5, '1'):
            self.refuse('invalid_input', lambda: budget.record_budget(self.store, dict(max_calls=maximum, reason='Bad'),
                expected_revision=self.store.revision, request_id='invalid'))
        self.refuse('invalid_input', lambda: budget.record_budget(self.store, dict(max_calls=2, reason=''),
            expected_revision=self.store.revision, request_id='empty'))
        self.refuse('review_budget_below_charged', lambda: budget.record_budget(self.store,
            dict(max_calls=0, reason='Too small'), expected_revision=self.store.revision, request_id='lower'))
        self.refuse('stale_revision', lambda: budget.record_budget(self.store, dict(max_calls=2, reason='Raise'),
            expected_revision=revision, request_id='stale'))
        raised = budget.record_budget(self.store, dict(max_calls=2, reason='Raise'),
            expected_revision=self.store.revision, request_id='raise')
        self.assertEqual(self.store.snapshot()['records']['review_budget']['store'], raised['result'])
        self.assertEqual(self.read_budget()['remaining_calls'], 1)
        self.invoke(identifier='second')
        self.refuse('review_budget_exhausted', lambda: self.invoke(identifier='third'))
        budget.record_budget(self.store, dict(max_calls=None, reason='Unlimited'),
            expected_revision=self.store.revision, request_id='unlimited')
        self.assertEqual(self.read_budget()['charged_calls'], 2)

    def test_rendered_usage_uses_the_same_canonical_inventory(self):
        self.set_budget(2)
        self.invoke()
        records = self.store.snapshot()['records']
        records = {kind: records[kind] for kind in ('review_budget', 'review_invocation', 'review_attempt')}
        files = output_views.compute_view_model(records, self.store.build_artifact_store(),
                                               output_views._ViewInputs({}, {}))
        path = 'intermediate/reviews/usage.json'
        self.assertIn(path, files, 'Review views must expose the allowance and canonical expenditure')
        self.assertEqual(json.loads(files[path].content)['review_budget'], self.read_budget())

    def test_unknown_usage_remains_unknown_in_rendered_reports(self):
        self.set_budget(1)
        self.invoke(observe=lambda *args: dict(status='failed', usage=None))
        report = protocol.usage_report(self.store.snapshot()['records'])
        self.assertEqual(report['unknown']['cost_usd'], 1)
        self.assertEqual(report['review_budget']['charged_calls'], 1)

    def test_allowance_requires_an_expected_revision(self):
        budget = importlib.import_module('research_harness.review_budget')
        self.refuse('invalid_input', lambda: budget.record_budget(self.store,
            dict(max_calls=1, reason='Require a revision'), expected_revision=None, request_id='missing-revision'))

    def test_allowance_cannot_overwrite_a_concurrent_edit_after_preparation(self):
        budget = importlib.import_module('research_harness.review_budget')
        revision = self.store.revision
        other = Store(self.root)
        connect = self.store._connection
        newer = dict(id='store', max_calls=2, reason='Concurrent edit')

        @contextmanager
        def interleave(*, writable=False):
            if writable:
                other.mutate('fixture', newer, lambda tx: tx.put('review_budget', 'store', newer),
                             expected_revision=revision, request_id='concurrent-edit')
            with connect(writable=writable) as connection:
                yield connection

        with patch.object(self.store, '_connection', side_effect=interleave):
            self.refuse('stale_revision', lambda: budget.record_budget(self.store,
                dict(max_calls=1, reason='Older edit'), expected_revision=revision, request_id='older-edit'))
        self.assertEqual(self.store.snapshot()['records']['review_budget']['store'], newer)

    def test_staged_usage_is_visible_before_an_attempt_and_deduplicated_after_it(self):
        self.set_budget(1)
        reservation = self.reserve()['result']
        report = protocol.usage_report(self.store.snapshot()['records'])
        self.assertEqual((report['calls'], report['pending_calls'], report['unknown']['cost_usd']), (1, 1, 1))
        usage = dict(input_tokens=10, output_tokens=5, cost_usd=0.25, wall_seconds=1)
        self.put('review_observation', reservation['id'], dict(reservation_id=reservation['id'],
            observation=dict(status='completed', usage=usage)))
        captured = protocol.usage_report(self.store.snapshot()['records'])
        self.assertEqual(captured['observed'], usage)
        self.assertEqual((captured['calls'], captured['pending_calls']), (1, 0))
        self.put('review_attempt', 'accepted', dict(id='accepted', assignment_id='assignment',
            reservation_id=reservation['id'], status='completed', usage=usage))
        self.assertEqual(protocol.usage_report(self.store.snapshot()['records']), captured)

    def test_non_object_historical_usage_remains_unknown(self):
        self.put('review_attempt', 'historical', dict(id='historical', assignment_id='assignment',
            origin='imported', status='failed', usage=['unknown']))
        report = protocol.usage_report(self.store.snapshot()['records'])
        self.assertEqual(report['unknown']['cost_usd'], 1)

    def test_staged_attempt_cannot_supply_usage_for_an_equal_ordinary_invocation_id(self):
        reservation = self.reserve()['result']
        self.put('review_invocation', 'same', dict(id='same', assignment_id='assignment', route_id='route'))
        self.put('review_attempt', 'same', dict(id='same', assignment_id='assignment',
            reservation_id=reservation['id'], status='completed', usage=dict(cost_usd=1)))
        report = protocol.usage_report(self.store.snapshot()['records'])
        self.assertEqual(report['observed']['cost_usd'], 1)
        self.assertEqual((report['calls'], report['pending_calls'], report['unknown']['cost_usd']), (2, 1, 1))

    def test_allowance_domain_supports_check_without_writing(self):
        budget = importlib.import_module('research_harness.review_budget')
        self.invoke()
        before = self.store.snapshot()
        payload = dict(max_calls=1, reason='One admitted call')
        checked = check_mutation(self.store, budget.record_budget, payload,
            expected_revision=self.store.revision, request_id='budget-check', bounded_payload=payload)
        self.assertEqual(checked['check'], 'accepted')
        self.assertFalse(checked['recorded'])
        self.assertEqual(self.store.snapshot(), before)
        too_small = dict(max_calls=0, reason='Below admitted work')
        self.refuse('review_budget_below_charged', lambda: check_mutation(self.store, budget.record_budget, too_small,
            expected_revision=self.store.revision, request_id='budget-check-small', bounded_payload=too_small))
        self.assertEqual(self.store.snapshot(), before)

    def test_an_imported_call_id_cannot_hide_a_second_admission(self):
        execution.record_capacity(self.store, dict(store_limit=10, route_limits={}), request_id='capacity')
        for index, maximum in enumerate((2, None)):
            with self.subTest(maximum=maximum):
                identifier = 'historical-' + str(index)
                self.put('review_attempt', identifier, dict(id=identifier, assignment_id='assignment',
                    origin='imported', status='failed', usage=None))
                self.set_budget(maximum)
                before = self.store.snapshot()
                self.refuse('record_conflict', lambda: self.invoke(identifier=identifier,
                    observe=lambda *args: self.fail('A charged identity must not launch another call')))
                self.assertEqual(self.store.snapshot(), before)


class ReviewBudgetRecoveryTests(TaskExecutionFixture):
    def test_completed_capture_accepts_and_reconciles_after_exhaustion(self):
        worker = workers.WorkerExecutionTests()
        worker.setUp()
        self.addCleanup(worker.doCleanups)
        worker.put('review_budget', 'store', dict(id='store', max_calls=1, reason='One capture'))
        reservation = worker.reserve()['result']
        process = worker.launch(reservation, 'after-capture')
        self.assertEqual(process.wait(timeout=60), 72)
        accepted = worker.accept(reservation)
        self.assertEqual(worker.accept(reservation), accepted)
        worker.reconcile(reservation)
        self.assertTrue(worker.current(reservation)['released'])
        worker.refuse('review_budget_exhausted', lambda: worker.reserve('b'))

    def test_uncaptured_worker_death_reconciles_without_refunding_the_call(self):
        worker = workers.WorkerExecutionTests()
        worker.setUp()
        self.addCleanup(worker.doCleanups)
        worker.put('review_budget', 'store', dict(id='store', max_calls=1, reason='One capture'))
        reservation = worker.reserve()['result']
        process = worker.launch(reservation, 'before-capture')
        self.assertEqual(process.wait(timeout=60), 71)
        worker.reconcile(reservation)
        self.assertEqual(worker.current(reservation)['state'], 'unknown')
        self.assertTrue(worker.current(reservation)['released'])
        worker.refuse('review_budget_exhausted', lambda: worker.reserve('b'))
