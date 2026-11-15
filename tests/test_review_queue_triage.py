"""Capacity diagnostics and autonomous drivers over synthetic verified routes."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from codex_route_fixtures import record_codex_route
from research_harness import cli, review_call_admission as admission
from research_harness import review_continuation, review_protocol as protocol
from research_harness import review_task_execution as execution, review_worker as worker
from research_harness.evidence import digest
from research_harness.review_stop import build_chain_scope
import test_research_problem_reviews as fixtures


REVIEWER_PROGRAM = r'''
import json, sys, time, unittest
from pathlib import Path
from unittest.mock import patch
from codex_route_fixtures import build_codex_response, install_codex_runtime
from research_harness import cli, review_series, review_transport, review_protocol
from research_harness.operations import immutable_record
from research_harness.storage import Store
root, assignment_id, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
store = Store(root)
fixture = unittest.TestCase()
install_codex_runtime(fixture)
if mode == 'series':
    (root / (assignment_id + '-ready')).touch()
    deadline = time.monotonic() + 30
    while len(list(root.glob('*-ready'))) < 3:
        assert time.monotonic() < deadline, 'The three drivers did not reach their barrier'
        time.sleep(.02)
def send(route, request, credential):
    (root / (assignment_id + '-started')).touch()
    if mode == 'hold':
        deadline = time.monotonic() + 60
        while not (root / (assignment_id + '-finish')).exists():
            assert time.monotonic() < deadline, 'The test did not finish the held reviewer'
            time.sleep(.02)
    elif len(store.snapshot()['records'].get('review_invocation', {})) == 1:
        deadline = time.monotonic() + 30
        while True:
            records = store.snapshot()['records']
            waits = admission.list_waiting_calls(records)
            if len(waits) == 2:
                report = cli._review_status(records, store.build_artifact_store())
                (root / 'queued.json').write_text(json.dumps(report['queue']))
                break
            assert time.monotonic() < deadline, 'The two other drivers did not ask for capacity'
            time.sleep(.02)
    if mode == 'series':
        return None
    output = json.loads((root / 'reviewer-output.json').read_text())
    return build_codex_response(route, request, output)
from research_harness import review_call_admission as admission
with patch.object(review_transport, 'send_request', side_effect=send):
    if mode == 'series':
        # A finite two-call reviewer stub keeps scheduling independent of the
        # scientific role's separate recording protocol. Admission, capture,
        # ownership, queue polling and driver lifecycle all stay real.
        from research_harness.review_continuation import _plan_capacity
        request = store.build_artifact_store().put(b'{"history":[],"packet":{}}', 'application/json')
        def operate(store, payload, **identity):
            def start(records, value):
                assignment = records['review_assignment'][value['assignment_id']]
                row = dict(value, route_id=assignment['route_id'], request=request, recorded_revision=0)
                return [immutable_record(records, 'review_invocation', value['id'], row)], row
            def observe(*args):
                send(None, None, None)
                return dict(status='completed', request=request, output=request, response=None, usage={})
            def finish(records, value, invocation, observation, revision):
                row = dict(invocation, **observation, recorded_revision=revision)
                return [immutable_record(records, 'review_attempt', value['id'], row)], row
            with patch.object(review_protocol, '_observed', side_effect=observe):
                return review_protocol._invoke_once(store, payload, 'review.invoke', start, finish,
                                                    credential=None, **identity)
        def plan(*args):
            snapshot = store.snapshot()
            records = snapshot['records']
            count = sum(row['assignment_id'] == assignment_id for row in records.get('review_attempt', {}).values())
            action = dict(state='done' if count == 2 else 'run', chain='fixture', head_id=assignment_id,
                progress=dict(attempts=count), command='review-run',
                payload=dict(id=assignment_id + '-call-' + str(count + 1), assignment_id=assignment_id),
                provider_usage=dict(windows=[], warnings=[]))
            return snapshot, action if count == 2 else _plan_capacity(records, records['review_assignment'][assignment_id], action)
        with patch.dict(cli.OPERATIONS, {'review-run': operate}), \
                patch.object(review_series, '_read_plan', side_effect=plan), \
                patch.object(review_series, 'render_workspace_views', return_value=None):
            sys.exit(review_series.run_series(store, assignment_id))
    cli.OPERATIONS['review-run'](store, dict(id='call-' + assignment_id, assignment_id=assignment_id),
                               expected_revision=store.revision, request_id='invoke-' + assignment_id)
'''


class QueueTriageTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.store, self.root = self.case.store, self.case.root
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        for route_id in ('route-a', 'route-b', 'idle-route'):
            record_codex_route(self.case, route_id)
        self.first = self.assign('bar-a', 'route-a', 'first')
        self.second = self.assign('bar-b', 'route-b', 'second')
        (self.root / 'reviewer-output.json').write_text(json.dumps(self.case.build_review_output('bar')))

    def assign(self, identifier, route_id, reviewer, *, role='bar', **extra):
        context = (self.case.deliver_supporting(reviewer) if role == 'bar' else
                   dict(artifact_refs=[self.case.supporting]))
        payload = dict(id=identifier, route_id=route_id, dossier_id=None, role=role,
                       reviewer_id=reviewer, author_id='author', context=context,
                       **extra)
        return self.case.mutate(protocol.record_assignment, payload)['result']

    def start(self, assignment):
        identifier = 'call-' + assignment['id']
        identity = worker._identity()
        authority = type('Authority', (), dict(reservation_id=execution.identify_call('review.invoke', identifier),
            token='fixture-token', identity=identity))()
        invocation = dict(id=identifier, assignment_id=assignment['id'], route_id=assignment['route_id'])
        revision = self.store.revision + 1
        def apply(tx):
            execution.start_call(tx, 'review.invoke', invocation, authority, revision)
            tx.put('review_invocation', identifier, invocation)
        self.store.mutate('fixture.start', invocation, apply, expected_revision=self.store.revision,
                          request_id='start-' + identifier)

    def enqueue(self, assignment, *, owner=None):
        records = self.case.read_records()
        return admission.record_wait(self.store, dict(operation='review.invoke',
            invocation_id='call-' + assignment['id'], route_id=assignment['route_id'], assignment_id=assignment['id'],
            scope=build_chain_scope(records, assignment['id'])), owner=owner)

    def project_queue(self):
        return cli._review_status(self.case.read_records(), self.case.artifacts)['queue']

    def await_condition(self, condition):
        deadline = time.monotonic() + 30
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'The reviewer did not reach the expected state')
            time.sleep(.02)

    def launch(self, assignment, mode):
        environment = dict(os.environ)
        paths = [str(Path(__file__).resolve().parent), environment.get('PYTHONPATH', '')]
        environment['PYTHONPATH'] = os.pathsep.join(path for path in paths if path)
        process = subprocess.Popen([sys.executable, '-c', REVIEWER_PROGRAM, str(self.root), assignment['id'], mode],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env=environment)
        def cleanup():
            (self.root / (assignment['id'] + '-finish')).touch()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=20)
        self.addCleanup(cleanup)
        return process

    def test_reviewer_child_imports_fixtures_without_inherited_pythonpath(self):
        with patch.dict(os.environ, {'PYTHONPATH': ''}):
            process = self.launch(self.first, 'hold')
        self.await_condition(lambda: process.poll() is not None or (self.root / 'bar-a-started').exists())
        if process.poll() is not None:
            out, err = process.communicate()
            self.fail(out + err)
        (self.root / 'bar-a-finish').touch()
        out, err = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, out + err)

    def test_planned_store_wait_names_its_limit_and_the_two_route_scheduler(self):
        self.start(self.first)
        before = self.store.snapshot()
        plan = review_continuation.plan_next(before['records'], self.case.artifacts, self.second['id'])
        self.assertEqual((plan['state'], plan['code']), ('wait', 'review_capacity_wait'))
        self.assertEqual(plan['details']['blocking_limits'], ['store_limit'])
        self.assertEqual(plan['details']['scheduler'], dict(store_limit=2, route_limits={'route-a': 1, 'route-b': 1}))
        self.assertEqual(self.store.snapshot(), before)

    def test_staged_wait_keeps_the_own_task_action_and_names_the_capacity_limit(self):
        self.start(self.first)
        action = dict(command='review-run', payload=dict(id='frame-call', assignment_id=self.second['id'],
                      epoch_id='epoch', task_id='frame'))
        plan = review_continuation._plan_staged_action(self.case.read_records(),
            dict(controller_assignment_id=self.second['id']),
            dict(task_id='frame', state='waiting', code='review_capacity_wait'), action,
            artifacts=self.case.artifacts)
        self.assertEqual(plan['payload'], action['payload'])
        self.assertEqual(plan['details']['blocking_limits'], ['store_limit'])
        self.assertEqual(plan['details']['scheduler'], dict(store_limit=2, route_limits={'route-a': 1, 'route-b': 1}))

    def test_queue_counts_only_verified_participants_and_keeps_stored_waits_unchanged(self):
        self.start(self.first)
        wait = self.enqueue(self.second)
        idle = dict(self.second, id='idle-wait', route_id='idle-route', slot_id=None)
        self.case.put('review_assignment', idle['id'], idle)
        self.enqueue(idle, owner=dict(pid=987654321, pgid=987654321, started='gone'))
        route = self.case.read_records()['review_route']['route-a']
        self.case.put('review_route', 'unverified', dict(route, id='unverified'))
        unverified = dict(self.second, id='unverified-wait', route_id='unverified', slot_id=None)
        self.case.put('review_assignment', unverified['id'], unverified)
        self.enqueue(unverified)
        before = self.store.snapshot()
        receipt = copy.deepcopy(self.store.committed_request(wait['id']))
        rows = {row['route_id']: row for row in self.project_queue()}
        self.assertEqual(rows['route-b']['blocking_limits'], ['store_limit'])
        self.assertEqual(rows['route-b']['scheduler'], dict(store_limit=2, route_limits={'route-a': 1, 'route-b': 1}))
        self.assertNotIn('scheduler', rows['unverified'])
        self.assertNotIn('idle-route', rows)
        self.assertEqual(self.store.snapshot(), before)
        execution.record_capacity(self.store, dict(store_limit=2, route_limits={'route-a': 1, 'route-b': 1}),
                                  request_id='raise-capacity')
        row = next(row for row in self.project_queue() if row['id'] == wait['id'])
        self.assertEqual(row['blocking_limits'], [])
        self.assertNotIn('scheduler', row)
        self.assertEqual(self.case.read_records()['review_call_wait'][wait['id']], wait)
        self.assertEqual(digest(self.store.committed_request(wait['id'])), digest(receipt))

    def test_route_wait_names_the_route_limit_without_suggesting_a_store_change(self):
        same_route = self.assign('same-route', 'route-a', 'third', role='standalone')
        execution.record_capacity(self.store, dict(store_limit=2, route_limits={'route-a': 1}), request_id='capacity')
        self.start(self.first)
        self.enqueue(same_route)
        plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, same_route['id'])
        row = self.project_queue()[0]
        for described in (plan['details'], row):
            self.assertEqual(described['blocking_limits'], ['route_limit'])
            self.assertNotIn('scheduler', described)

    def test_same_route_wait_reports_both_limits_and_no_scheduler(self):
        same_route = self.assign('same-route', 'route-a', 'third', role='standalone')
        self.start(self.first)
        self.enqueue(same_route)
        plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, same_route['id'])
        for described in (plan['details'], self.project_queue()[0]):
            self.assertEqual(described['blocking_limits'], ['store_limit', 'route_limit'])
            self.assertNotIn('scheduler', described)

    def test_fifo_wait_does_not_claim_that_free_capacity_is_full(self):
        self.enqueue(self.first)
        self.enqueue(self.second)
        plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, self.second['id'])
        self.assertEqual(plan['state'], 'wait')
        for described in (plan['details'],) + tuple(self.project_queue()):
            self.assertEqual(described['blocking_limits'], [])
            self.assertNotIn('scheduler', described)

    def test_recording_the_suggested_scheduler_allows_two_verified_routes_to_run_together(self):
        first = self.launch(self.first, 'hold')
        self.await_condition(lambda: (self.root / 'bar-a-started').exists())
        second = self.launch(self.second, 'hold')
        self.await_condition(lambda: bool(self.case.read_records().get('review_call_wait')))
        row = self.project_queue()[0]
        self.assertEqual(row['blocking_limits'], ['store_limit'])
        scheduler = row['scheduler']
        self.assertEqual(scheduler, dict(store_limit=2, route_limits={'route-a': 1, 'route-b': 1}))
        self.assign('policy-assignment', 'route-b', 'third', role='standalone', scheduler=scheduler)
        self.await_condition(lambda: (self.root / 'bar-b-started').exists())
        self.assertIsNone(first.poll())
        self.assertIsNone(second.poll())
        records = self.case.read_records()
        active = [row for row in records['review_pool_membership'].values() if not row['released']]
        self.assertEqual({row['route_id'] for row in active}, {'route-a', 'route-b'})
        for assignment, process in ((self.first, first), (self.second, second)):
            (self.root / (assignment['id'] + '-finish')).touch()
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, out + err)

    def test_three_series_drivers_finish_in_request_order_without_external_commands(self):
        # The reviewer, not the parent, waits until all three drivers have asked.
        second = self.assign('same-route-second', 'route-a', 'fourth', role='standalone')
        third = self.assign('same-route-third', 'route-a', 'fifth', role='standalone')
        processes = [self.launch(assignment, 'series') for assignment in (self.first, second, third)]
        for process in processes:
            out, err = process.communicate(timeout=90)
            self.assertEqual(process.returncode, 0, out + err)
            self.assertEqual(json.loads(out.splitlines()[-1])['state'], 'done')
        records = self.case.read_records()
        self.assertNotIn('review_series_stop', records)
        drivers = list(records['review_series_run'].values())
        self.assertEqual(len(drivers), 3)
        self.assertTrue(all(row['ended']['state'] == 'done' for row in drivers))
        calls = [row for row in records['review_pool_membership'].values() if row['operation'] == 'review.invoke']
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(row['released'] for row in calls))
        waits = {row['invocation_id']: row for row in records['review_call_wait'].values()}
        self.assertGreaterEqual(len(waits), 2)
        self.assertTrue(all(row['state'] == 'admitted' for row in waits.values()))
        created = {row['invocation_id']: records['review_invocation'][row['invocation_id']]['recorded_revision']
                   for row in calls}
        starts = sorted(calls, key=lambda row: created[row['invocation_id']])
        requests = sorted(calls, key=lambda row: waits.get(row['invocation_id'], {}).get(
            'requested_revision', created[row['invocation_id']]))
        self.assertEqual([row['invocation_id'] for row in starts], [row['invocation_id'] for row in requests])
        self.assertEqual(len(records['review_attempt']), 6)
        queue = json.loads((self.root / 'queued.json').read_text())
        self.assertEqual(len(queue), 2)
        self.assertTrue(all(row['blocking_limits'] == ['store_limit', 'route_limit'] for row in queue))


if __name__ == '__main__':
    unittest.main()
