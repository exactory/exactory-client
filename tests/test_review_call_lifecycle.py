"""Public calls retain capacity until their owned execution has terminated."""

import json
import subprocess
import sys
import time
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import cli, review_protocol as protocol, review_output_correction as correction
from research_harness import review_task_execution as execution, review_worker
from research_harness.errors import ResearchError
from research_harness.storage import Store


_PROGRAM = r'''
import json, os, subprocess, sys, time
from pathlib import Path
from unittest.mock import patch
from research_harness import cli, review_protocol as protocol, review_output_correction as correction
from research_harness import review_codex_transport as codex
from research_harness.storage import Store
root, command, payload, mode = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]
store = Store(root)
codex.runtime = lambda: {'adapter':'codex_cli_v1','fixture':'reviewer-excerpt-test'}
codex.NATIVE_DECLARATIONS = ()
codex.NATIVE_DECLARATION_SETS = {()}
def observe(*args):
    if mode == 'child':
        process = subprocess.Popen([sys.executable, '-c',
            "import os,sys,time\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(str(os.getpid()))\ndeadline=time.monotonic()+30\nwhile not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)",
            str(root / 'child-pid'), str(root / 'release-child')], start_new_session=True)
        while not (root / 'child-pid').exists(): time.sleep(.01)
    (root / 'in-call').touch()
    if mode == 'live':
        while not (root / 'release').exists(): time.sleep(.01)
        return {'status':'failed', 'request':None, 'response':None, 'output':None}
    os._exit(73)
with patch.object(protocol, '_observed', side_effect=observe), \
     patch.object(correction, 'prepare_packet', return_value={'diagnostic':{}}), \
     patch.object(correction, '_route', side_effect=lambda records, artifacts, assignment, route_id: records['review_route'][route_id]):
    cli.OPERATIONS[command](store, payload, expected_revision=store.revision, request_id='interrupted-' + command)
'''


class PublicCallLifecycleTests(ResultPartsCase):
    def call_payload(self, command, identifier='owned'):
        if command == 'review-probe':
            return dict(id=identifier, route_id='route')
        assignment = self.assign(identifier + '-assignment', reviewer='second' if command == 'review-run' else 'first')
        payload = dict(id=identifier, assignment_id=assignment['id'])
        if command == 'review-output-correction':
            payload['route_id'] = 'route'
        return payload

    def command(self, command, payload, request_id):
        with patch.object(correction, 'prepare_packet', return_value={'diagnostic':{}}), \
                patch.object(correction, '_route', side_effect=lambda records, artifacts, assignment, route_id:
                             records['review_route'][route_id]):
            return cli.OPERATIONS[command](self.case.store, payload,
                expected_revision=self.case.store.revision, request_id=request_id)

    def wait_file(self, path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(path.exists(), str(path))

    def launch(self, command, payload, mode='die'):
        process = subprocess.Popen([sys.executable, '-c', _PROGRAM, str(self.case.root), command,
                                    json.dumps(payload), mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        self.wait_file(self.case.root / 'in-call')
        return process

    def reconcile(self, command, payload, request_id='reconcile'):
        return self.command('review-run', dict(action='reconcile', operation={
            'review-run':'review.invoke', 'review-probe':'review.route_probe',
            'review-output-correction':'review.output_correction'}[command], invocation_id=payload['id']), request_id)

    def blocked_probe(self):
        with patch.object(protocol, '_observed', side_effect=AssertionError('Occupied calls must prevent model I/O')):
            self.assert_code('review_capacity_wait', lambda: self.command('review-probe',
                dict(id='blocked', route_id='route'), 'blocked'))

    def test_interrupted_public_probes_corrections_and_ordinary_calls_can_be_reconciled(self):
        for command, completed in (('review-probe', 'review_route_probe'),
                                   ('review-output-correction', 'review_output_correction'),
                                   ('review-run', 'review_attempt')):
            with self.subTest(command=command):
                marker = self.case.root / 'in-call'
                marker.unlink(missing_ok=True)
                payload = self.call_payload(command, identifier=command)
                process = self.launch(command, payload)
                self.assertEqual(process.wait(timeout=5), 73)
                self.case.store = Store(self.case.root)
                self.blocked_probe()
                self.assert_code('review_invocation_pending', lambda: self.command(command, payload, 'interrupted-' + command))
                result = self.reconcile(command, payload, request_id='reconcile-' + command)
                self.assertTrue(result['result']['released'])
                self.assertTrue(result['result']['termination']['confirmed'])
                records = self.case.read_records()
                failure = records[completed][payload['id']]
                self.assertEqual((failure['origin'], failure['status']), ('execution', 'failed'))
                self.assertIsNone(failure['output'])
                self.assertIn(payload['id'], protocol.usage_report(records)['call_ids'])
                self.assertEqual(self.reconcile(command, payload, request_id='reconcile-' + command), result)
                reconciliation = dict(action='reconcile', operation={
                    'review-run':'review.invoke', 'review-probe':'review.route_probe',
                    'review-output-correction':'review.output_correction'}[command], invocation_id=payload['id'])
                path = self.case.root.parent / 'reconcile.json'
                path.write_text(json.dumps(reconciliation))
                arguments = ['review-run', '--workspace', str(self.case.root), '--file', str(path),
                    '--expected-revision', str(self.case.store.revision), '--request-id', 'reconcile-' + command, '--check']
                self.assertEqual(cli.run(cli.build_parser().parse_args(arguments)), result)
                with patch.object(protocol, '_observed', return_value=dict(status='failed', request=None,
                        response=None, output=None)):
                    self.command('review-probe', dict(id='after-' + command, route_id='route'), 'after-' + command)
                if command == 'review-output-correction':
                    self.assert_code('review_correction_exhausted', lambda: self.command(command,
                        dict(payload, id='second-correction'), 'second-correction'))

    def test_public_reconciliation_does_not_release_a_live_probe(self):
        payload = self.call_payload('review-probe')
        process = self.launch('review-probe', payload, 'live')
        self.assert_code('review_worker_reconciliation_required', lambda: self.reconcile('review-probe', payload))
        self.blocked_probe()
        (self.case.root / 'release').touch()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertTrue(self.reconcile('review-probe', payload, 'after-exit')['result']['released'])

    def test_orphan_transport_child_keeps_capacity_until_its_group_terminates(self):
        payload = self.call_payload('review-probe')
        process = self.launch('review-probe', payload, 'child')
        self.assertEqual(process.wait(timeout=5), 73)
        def terminate():
            (self.case.root / 'release-child').touch()
        self.addCleanup(terminate)
        self.assertFalse(self.reconcile('review-probe', payload)['result']['released'])
        self.blocked_probe()
        terminate()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.reconcile('review-probe', payload, 'child-exit-' + str(self.case.store.revision))
            if result['result']['released']:
                break
            time.sleep(.01)
        self.assertTrue(result['result']['released'])

    def test_imported_attempt_cannot_take_a_running_ordinary_completion_id(self):
        assignment = self.assign('ordinary')
        payload = dict(id='ordinary-attempt', assignment_id=assignment['id'])
        def observe(*args):
            membership = self.case.read_records()['review_pool_membership'][execution.identify_call('review.invoke', payload['id'])]
            self.assertTrue(membership['active'])
            self.assertFalse(membership['released'])
            imported = dict(payload, status='failed', output=None, reason='Imported failed attempt', usage={})
            self.assert_code('review_invocation_pending', lambda: protocol.record_attempt(self.case.store, imported,
                expected_revision=self.case.store.revision, request_id='import-live'))
            self.blocked_probe()
            return dict(status='failed', request=None, response=None, output=None)
        with patch.object(protocol, '_observed', side_effect=observe):
            result = self.command('review-run', payload, 'ordinary-run')
        self.assertEqual(result['result']['origin'], 'instrumented')
        self.assertEqual(self.case.read_records()['review_attempt'][payload['id']], result['result'])

    def test_a_committed_completion_keeps_capacity_while_a_child_group_survives(self):
        self.keep_capacity_with_child(escape=False)

    def test_a_completed_call_keeps_capacity_while_an_escaped_child_holds_ownership(self):
        self.keep_capacity_with_child(escape=True)

    def keep_capacity_with_child(self, *, escape):
        payload = self.call_payload('review-probe')
        def terminate():
            (self.case.root / 'release-group').touch()
        self.addCleanup(terminate)
        def observe(*args):
            child = "import sys,time\nfrom pathlib import Path\ndeadline=time.monotonic()+30\nwhile not Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.01)"
            program = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]], "
                       "close_fds=" + repr(not escape) + ", start_new_session=" + repr(escape) +
                       ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")
            subprocess.run([sys.executable, '-c', program, str(self.case.root / 'release-group'), child],
                           start_new_session=True, check=True)
            return dict(status='failed', request=None, response=None, output=None)
        with patch.object(protocol, '_observed', side_effect=observe):
            self.command('review-probe', payload, 'completed-with-child')
        current = self.case.read_records()['review_pool_membership'][execution.identify_call('review.route_probe', payload['id'])]
        self.assertFalse(current['released'])
        self.assertEqual(len(current['children']), 1)
        self.assertFalse(self.reconcile('review-probe', payload)['result']['released'])
        self.blocked_probe()
        terminate()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.reconcile('review-probe', payload, 'group-exit-' + str(self.case.store.revision))
            if result['result']['released']:
                break
            time.sleep(.01)
        self.assertTrue(result['result']['released'])

    def test_old_imported_completion_does_not_release_a_live_owned_call(self):
        payload = self.call_payload('review-run')
        def observe(*args):
            self.case.put('review_attempt', payload['id'], dict(payload, origin='imported', status='failed'))
            self.blocked_probe()
            raise KeyboardInterrupt()
        with patch.object(protocol, '_observed', side_effect=observe):
            with self.assertRaises(KeyboardInterrupt):
                self.command('review-run', payload, 'historical-import')
        self.assertTrue(self.reconcile('review-run', payload)['result']['released'])

    def test_unavailable_termination_check_preserves_the_live_observation(self):
        payload = self.call_payload('review-probe')
        with patch.object(protocol, '_observed', return_value=dict(status='failed', request=None,
                response=None, output=None)), patch.object(review_worker, '_process_table',
                side_effect=ResearchError('review_worker_reconciliation_required', 'Kernel check unavailable')):
            result = self.command('review-probe', payload, 'unchecked-termination')
        records = self.case.read_records()
        self.assertEqual(records['review_route_probe'][payload['id']], result['result'])
        self.assertFalse(records['review_pool_membership'][execution.identify_call('review.route_probe', payload['id'])]['released'])
        self.assertTrue(self.reconcile('review-probe', payload)['result']['released'])

    def test_imported_completion_cannot_enable_a_parallel_retry_with_spare_capacity(self):
        payload = self.call_payload('review-run')
        execution.record_capacity(self.case.store, dict(store_limit=2, route_limits={}), request_id='two-slots')
        def observe(*args):
            self.case.put('review_attempt', payload['id'], dict(payload, origin='imported', status='failed'))
            with patch.object(protocol, '_observed', side_effect=AssertionError('A live task cannot be retried')):
                self.assert_code('review_invocation_pending', lambda: self.command('review-run',
                    dict(payload, id='parallel-retry'), 'parallel-retry'))
            raise KeyboardInterrupt()
        with patch.object(protocol, '_observed', side_effect=observe):
            with self.assertRaises(KeyboardInterrupt):
                self.command('review-run', payload, 'ordinary-import')

    def test_public_python_reconciliation_can_replay_without_an_expected_revision(self):
        payload = self.call_payload('review-probe')
        with patch.object(protocol, '_observed', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.command('review-probe', payload, 'interrupted-probe')
        reconciliation = dict(operation='review.route_probe', invocation_id=payload['id'])
        result = execution.reconcile_call(self.case.store, reconciliation, request_id='no-cas-reconcile')
        self.assertEqual(execution.reconcile_call(self.case.store, reconciliation,
            request_id='no-cas-reconcile'), result)

    def test_local_auxiliary_processes_are_owned_when_the_caller_omits_a_group(self):
        payload = self.call_payload('review-probe')
        def observe(*args):
            result = subprocess.run([sys.executable, '-c', 'print(42)'], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), '42')
            return dict(status='failed', request=None, response=None, output=None)
        with patch.object(protocol, '_observed', side_effect=observe):
            result = self.command('review-probe', payload, 'auxiliary-process')
        current = self.case.read_records()['review_pool_membership'][execution.identify_call('review.route_probe', payload['id'])]
        self.assertTrue(current['released'])
        self.assertFalse(current['active'])
        self.assertEqual(current['recorded_revision'], result['revision'])
        self.assertEqual(len(current['children']), 1)
        self.assertEqual(current['children'][0]['pid'], current['children'][0]['pgid'])
