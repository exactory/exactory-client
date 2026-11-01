"""Upgrade recovery uses historical records without inventing process ownership."""

from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import cli, review_protocol as protocol
from research_harness import review_output_correction as correction
from research_harness import review_task_execution as execution, review_worker
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class HistoricalCallTests(ResultPartsCase):
    def command(self, name, payload, request_id):
        with patch.object(correction, 'prepare_packet', return_value={'diagnostic': {}}), \
                patch.object(correction, '_route', side_effect=lambda records, artifacts, assignment, route_id:
                             records['review_route'][route_id]):
            return cli.OPERATIONS[name](self.case.store, payload,
                expected_revision=self.case.store.revision, request_id=request_id)

    def interrupt_historical_call(self, name):
        payload = dict(id='historical-' + name)
        if name == 'review-probe':
            payload['route_id'] = 'route'
        else:
            payload['assignment_id'] = self.assign('historical-assignment')['id']
            if name == 'review-output-correction':
                payload['route_id'] = 'route'
        # 0.69.17 uses these same invocation records, without start_call's
        # review_pool_membership. Only the newly added ownership write is omitted.
        with patch.object(execution, 'start_call', return_value=None), \
                patch.object(protocol, '_observed', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.command(name, payload, 'historical-start')
        return payload

    def import_historical_attempt(self, payload, status='failed'):
        imported = dict(payload, status=status, output=None,
            reason='The interrupted invocation has no retained observation.',
            usage=dict(input_tokens=None, output_tokens=None, wall_seconds=None))
        # review-attempt was the documented 0.69.17 reconciliation command.
        # That release did not have the new active-invocation import guard.
        with patch.object(execution, 'has_call_terminated', return_value=True):
            return self.command('review-attempt', imported, 'historical-import')['result']

    def reconcile(self, operation, payload, request_id='reconcile'):
        return self.command('review-run', dict(action='reconcile', operation=operation,
            invocation_id=payload['id']), request_id)

    def assert_capacity_occupied(self):
        with patch.object(protocol, '_observed', side_effect=AssertionError('Capacity must prevent I/O')):
            self.assert_code('review_capacity_wait', lambda: self.command('review-probe',
                dict(id='blocked', route_id='route'), 'blocked'))

    def test_documented_06917_imported_failure_stays_closed_after_upgrade(self):
        payload = self.interrupt_historical_call('review-run')
        imported = self.import_historical_attempt(payload)
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                response=None, request=None)):
            admitted = self.command('review-probe', dict(id='after-upgrade', route_id='route'), 'after-upgrade')
        self.assertEqual(admitted['result']['status'], 'failed')
        reconciled = self.reconcile('review.invoke', payload)
        self.assertTrue(reconciled['result']['released'])
        self.assertEqual(reconciled['result']['state'], 'historical_closed')
        self.assertEqual(reconciled['result']['outcome'], 'unknown')
        self.assertIsNone(reconciled['result']['termination'])
        self.assertEqual(reconciled['result']['evidence'], dict(kind='review_attempt', key=payload['id'],
            digest=digest(imported)))
        self.assertEqual(self.case.read_records()['review_attempt'][payload['id']], imported)
        self.assertEqual(self.reconcile('review.invoke', payload), reconciled)
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                response=None, request=None)):
            retry = self.command('review-run', dict(payload, id='retry'), 'retry')
        self.assertEqual(retry['result']['origin'], 'instrumented')

    def check_missing_termination(self, name, operation, invocation_kind, *, imported_completion=False):
        payload = self.interrupt_historical_call(name)
        if imported_completion:
            self.import_historical_attempt(payload, status='completed')
        self.assert_capacity_occupied()
        before = self.case.store.snapshot()
        with patch.object(review_worker, '_process_table', side_effect=AssertionError(
                'An empty identity set cannot establish historical termination')):
            with self.assertRaises(ResearchError) as failure:
                self.reconcile(operation, payload)
        self.assertEqual(failure.exception.code, 'review_worker_reconciliation_required')
        details = failure.exception.details
        invocation = before['records'][invocation_kind][payload['id']]
        self.assertEqual(details['invocation'], dict(kind=invocation_kind, key=payload['id'],
            digest=digest(invocation), fields=sorted(invocation)))
        self.assertEqual(details['missing'], ['review_pool_membership', 'owner_process_identity',
                                            'transport_process_groups'])
        if imported_completion:
            self.assertEqual(details['completion']['kind'], 'review_attempt')
            self.assertEqual(details['completion']['key'], payload['id'])
        else:
            self.assertIsNone(details['completion'])
        self.assertEqual(self.case.store.snapshot(), before)
        self.assert_capacity_occupied()

    def test_open_historical_ordinary_call_reports_missing_termination_evidence(self):
        self.check_missing_termination('review-run', 'review.invoke', 'review_invocation')

    def test_open_historical_probe_reports_missing_termination_evidence(self):
        self.check_missing_termination('review-probe', 'review.route_probe', 'review_probe_invocation')

    def test_open_historical_correction_reports_missing_termination_evidence(self):
        self.check_missing_termination('review-output-correction', 'review.output_correction',
                                      'review_output_correction_invocation')

    def test_imported_completion_alone_cannot_prove_historical_termination(self):
        self.check_missing_termination('review-run', 'review.invoke', 'review_invocation',
                                      imported_completion=True)
