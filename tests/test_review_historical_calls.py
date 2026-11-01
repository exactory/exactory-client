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
        return self.command('review-attempt', imported, 'historical-import-' + payload['id'])['result']

    def reconcile(self, operation, payload, request_id='reconcile'):
        return self.command('review-run', dict(action='reconcile', operation=operation,
            invocation_id=payload['id']), request_id)

    def assert_probe_admitted(self):
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                response=None, request=None)):
            result = self.command('review-probe', dict(id='admitted', route_id='route'), 'admitted')
        self.assertEqual(result['result']['status'], 'failed')

    def assert_import_step(self, details, payload):
        self.assertEqual(details['invocation_id'], payload['id'])
        self.assertEqual(details['assignment_id'], payload['assignment_id'])
        step = details['next']
        self.assertEqual(step['command'], 'review-attempt')
        self.assertEqual(step['payload']['id'], payload['id'])
        self.assertEqual(step['payload']['assignment_id'], payload['assignment_id'])
        self.assertEqual(step['payload']['status'], 'failed')
        self.assertIsNone(step['payload']['output'])
        self.assertTrue(step['payload']['reason'])
        self.assertEqual(step['payload']['usage'], dict(input_tokens=None, output_tokens=None,
                                                       cost_usd=None, wall_seconds=None))
        return step

    def test_open_historical_ordinary_call_does_not_block_another_assignment_at_capacity_one(self):
        self.interrupt_historical_call('review-run')
        assignment = self.assign('other-assignment', reviewer='second')
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                response=None, request=None)):
            result = self.command('review-run', dict(id='other-call', assignment_id=assignment['id']),
                                  'other-call')
        self.assertEqual(result['result']['origin'], 'instrumented')

    def test_historical_pending_assignment_names_executable_import_then_allows_new_id_retry(self):
        payload = self.interrupt_historical_call('review-run')
        before = self.case.store.snapshot()
        with patch.object(protocol, '_observed', side_effect=AssertionError('Pending assignment must prevent I/O')):
            error = self.assert_code('review_invocation_pending', lambda: self.command('review-run',
                dict(payload, id='retry'), 'retry'))
        self.assertEqual(self.case.store.snapshot(), before)
        self.assertIn('review-attempt', error.message)
        step = self.assert_import_step(error.details, payload)
        imported = self.command(step['command'], step['payload'], 'documented-import')['result']
        self.assertEqual(imported['origin'], 'imported')
        receipt = self.reconcile('review.invoke', payload)['result']
        self.assertEqual(receipt['state'], 'historical_closed')
        self.assertEqual(receipt['outcome'], 'unknown')
        self.assertIsNone(receipt['termination'])
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                response=None, request=None)):
            self.assertEqual(self.command('review-run', dict(payload, id='retry'), 'retry')
                             ['result']['origin'], 'instrumented')
        self.assert_code('review_invocation_pending', lambda: self.command('review-run', payload, 'historical-start'))

    def test_owned_call_still_refuses_import_until_confirmed_termination(self):
        payload = dict(id='owned', assignment_id=self.assign('owned-assignment')['id'])
        with patch.object(protocol, '_observed', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.command('review-run', payload, 'owned-start')
        self.assert_code('review_invocation_pending', lambda: self.import_historical_attempt(payload))
        receipt = self.reconcile('review.invoke', payload)['result']
        self.assertTrue(receipt['termination']['confirmed'])
        attempt = self.case.read_records()['review_attempt'][payload['id']]
        self.assertEqual(attempt['origin'], 'execution')

    def test_historical_incomplete_attempt_ends_pending_state_as_in_06917(self):
        payload = self.interrupt_historical_call('review-run')
        self.case.put('review_attempt', payload['id'], dict(payload, origin='imported', status='incomplete',
            output=None, reason='The process stopped during the call.', usage={}, recorded_revision=1))
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                response=None, request=None)):
            result = self.command('review-run', dict(payload, id='retry'), 'retry')
        self.assertEqual(result['result']['origin'], 'instrumented')

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

    def check_missing_termination(self, name, operation, invocation_kind):
        payload = self.interrupt_historical_call(name)
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
        self.assertIsNone(details['completion'])
        if operation == 'review.invoke':
            self.assert_import_step(details, payload)
        else:
            self.assertNotIn('next', details)
        self.assertEqual(self.case.store.snapshot(), before)
        self.assert_probe_admitted()

    def test_open_historical_ordinary_call_reports_missing_termination_evidence(self):
        self.check_missing_termination('review-run', 'review.invoke', 'review_invocation')

    def test_open_historical_probe_reports_missing_termination_evidence(self):
        self.check_missing_termination('review-probe', 'review.route_probe', 'review_probe_invocation')

    def test_open_historical_correction_reports_missing_termination_evidence(self):
        self.check_missing_termination('review-output-correction', 'review.output_correction',
                                      'review_output_correction_invocation')

    def test_historical_same_id_attempt_of_any_status_closes_without_proving_termination(self):
        payload = self.interrupt_historical_call('review-run')
        # These attempts were already imported under 0.69.17, before upgrade.
        prior = dict(payload, origin='imported', status='failed', output=None,
                     reason='The process stopped during the call.', usage={}, recorded_revision=1)
        self.case.put('review_attempt', payload['id'], prior)
        for status in ('failed', 'incomplete', 'completed'):
            with self.subTest(status=status):
                # Build a distinct old invocation of the same authenticated shape
                # for each status, without reopening or replacing an attempt.
                identifier = payload['id'] + '-' + status
                invocation = dict(self.case.read_records()['review_invocation'][payload['id']], id=identifier)
                self.case.put('review_invocation', identifier, invocation)
                imported = dict(prior, id=identifier, status=status)
                self.case.put('review_attempt', identifier, imported)
                result = self.reconcile('review.invoke', dict(id=identifier), 'reconcile-' + status)['result']
                self.assertEqual(result['state'], 'historical_closed')
                self.assertEqual(result['outcome'], 'unknown')
                self.assertIsNone(result['termination'])
                self.assertEqual(result['evidence'], dict(kind='review_attempt', key=identifier,
                                                         digest=digest(imported)))
                if status == 'incomplete':
                    with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None,
                            response=None, request=None)):
                        self.command('review-run', dict(payload, id='retry-incomplete'), 'retry-incomplete')
        self.assert_probe_admitted()
