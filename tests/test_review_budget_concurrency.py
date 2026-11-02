"""Independent processes cannot spend the same final cumulative call."""

import json
import subprocess
import sys

from research_harness import review_task_execution as execution
from test_review_budget import BudgetFixture


class ReviewBudgetConcurrencyTests(BudgetFixture):
    def race(self, modes):
        program = '''import json, sys, time
from pathlib import Path
from research_harness import review_protocol as protocol, review_task_execution as execution
from research_harness.errors import ResearchError
from research_harness.operations import immutable_record
from research_harness.storage import Store
root, mode, payload = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
store = Store(root)
def start(records, value):
    row = dict(id=mode, route_id='route', assignment_id='assignment', request=payload['request'])
    return [immutable_record(records, 'review_invocation', mode, row)], row
def finish(records, value, invocation, observation, revision):
    row = dict(invocation, **observation)
    return [immutable_record(records, 'review_attempt', mode, row)], row
protocol._observed = lambda *args: dict(status='failed')
(root / ('ready-' + mode)).touch()
while not (root / 'gate').exists(): time.sleep(.005)
for retry in range(20):
    try:
        if mode == 'ordinary':
            protocol._invoke_once(store, dict(id=mode), 'review.invoke', start, finish,
                expected_revision=store.revision, request_id=mode, credential=None)
        else:
            execution.reserve_task(store, payload, request_id=mode)
        print(json.dumps({'admitted': True}), flush=True)
        break
    except ResearchError as error:
        if error.code == 'stale_revision': continue
        print(json.dumps({'error': error.code}), flush=True)
        break
else: raise RuntimeError('Admission did not settle')
'''
        processes = [subprocess.Popen([sys.executable, '-c', program, str(self.root), mode,
            json.dumps(self.payload(name))], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for mode, name in modes]
        try:
            # Both children must reach the same admission barrier independently.
            import time
            deadline = time.monotonic() + 60
            while not all((self.root / ('ready-' + mode)).exists() for mode, _ in modes):
                self.assertLess(time.monotonic(), deadline, 'Children did not reach admission')
                time.sleep(.01)
            (self.root / 'gate').touch()
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(json.loads(stdout))
            return results
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=60)

    def check_last_call(self, modes):
        self.set_budget(2)
        self.invoke(identifier='already-spent')
        execution.record_capacity(self.store, dict(store_limit=10, route_limits={}), request_id='capacity')
        results = self.race(modes)
        self.assertEqual(sum(row.get('admitted', False) for row in results), 1, results)
        self.assertEqual([row['error'] for row in results if 'error' in row], ['review_budget_exhausted'])
        self.assertEqual(self.read_budget()['charged_calls'], 2)

    def test_two_processes_compete_for_the_last_staged_admission(self):
        self.check_last_call((('staged-a', 'a'), ('staged-b', 'b')))

    def test_ordinary_and_staged_processes_compete_for_the_last_admission(self):
        self.check_last_call((('ordinary', 'a'), ('staged', 'b')))
