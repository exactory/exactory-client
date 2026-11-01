"""Operator-planned retrievals reach a recorded result or bounded incompleteness."""

import copy
import json
import sys
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import review_protocol as protocol, review_worker
from research_harness.evidence import digest
import test_review_staged_frontier as frontier


WORKER = frontier.WORKER.replace('from test_review_staged_frontier import build_output',
                                'from test_review_staged_retrieval_journey import build_output')


def build_output(body, root):
    assigned = body['task']
    if assigned['type'] == 'final' and body.get('retrieval_observations'):
        return dict(retrievals=[dict(copy.deepcopy(row), status='pending', answer=None, observation_ids=[])
            if (root / 'leave-pending').exists() else dict(copy.deepcopy(row), status='resolved',
                answer='The reread original explicitly states the bounded hypothesis.',
                observation_ids=body['retrieval_observations']) for row in body['retrieval']])
    if assigned['type'] == 'final' and not (root / 'retrieve').exists():
        (root / 'retrieve').touch()
        return dict(retrievals=[dict(id='original-hypothesis', question='Does the original state the bounded hypothesis?',
            inputs=[copy.deepcopy(row['span']) for row in body['originals']], claim_ids=assigned['claim_ids'],
            context_roots=assigned['context_roots'], status='pending', answer=None, observation_ids=[],
            requested_by=assigned['id'])])
    return frontier.build_output(body, root)


class RetrievalJourneyTests(ResultPartsCase):
    staged_entry = True
    output_tokens = 4000
    timeout_seconds = 30
    create_controller = frontier.FrontierTests.create_controller
    command = frontier.FrontierTests.command

    def prepare_journey(self, leave_pending=False):
        controller = self.create_controller()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        if leave_pending:
            (self.case.root / 'leave-pending').touch()
        return controller, output

    def run_journey(self, controller):
        dispatched = []
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            for _ in range(30):
                planned = self.command('review-assignment', {'controller_assignment_id': controller['id']})
                if planned.get('outcome'):
                    return planned, dispatched
                next_work = planned['next']
                self.assertTrue(next_work, 'A pending retrieval journey must name its next operation')
                work = next_work[0]
                if work['command'] == 'value-review':
                    self.command('value-review', work['payload'])
                    return planned, dispatched
                self.assertEqual(work['command'], 'review-run')
                assigned = self.case.read_records()['review_task'][work['payload']['task_id']]
                self.assertEqual(assigned['controller_assignment_id'], controller['id'])
                self.assertEqual(assigned['task']['planned_request_digest'],
                                 digest(json.loads(self.case.artifacts.read(assigned['request']))))
                dispatched.append(assigned)
                self.command('review-run', work['payload'])
        self.fail('The operator retrieval journey exceeded its finite frontier')

    def test_pending_retrieval_is_reread_resolved_and_recorded_through_operator_commands(self):
        controller, output = self.prepare_journey()
        planned, dispatched = self.run_journey(controller)
        self.assertEqual(planned['next'][0]['command'], 'value-review')
        records = self.case.read_records()
        rereads = [row for row in dispatched if row['task']['type'] == 'frame'
                   and any(key.startswith('retrieval:') for key in row['task']['obligation_ids'])]
        self.assertEqual(len(rereads), 1)
        reread = rereads[0]
        resolvers = [row for row in dispatched if row['task']['type'] == 'final'
                     and any(key.startswith('retrieval:') for key in row['task']['obligation_ids'])]
        self.assertEqual(len(resolvers), 1)
        resolver = resolvers[0]
        packet = json.loads(self.case.artifacts.read(resolver['packet']))['staged_review']
        read = next(row['receipt']['staged_observation'] for row in records['review_task_acceptance'].values()
                    if row['task_id'] == reread['id'])
        resolved = next(row['receipt']['staged_observation'] for row in records['review_task_acceptance'].values()
                        if row['task_id'] == resolver['id'])
        self.assertEqual(packet['retrieval_observations'], [read['id']])
        self.assertEqual(resolved['output']['retrievals'][0]['status'], 'resolved')
        self.assertEqual(resolved['output']['retrievals'][0]['observation_ids'], [read['id']])
        self.assertIn(reread['id'], resolver['task']['dependencies'])
        state = protocol.assignment_state(records, self.case.artifacts, controller['id'])
        self.assertEqual(state['effective']['effective_output'], output)
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'complete')
        self.assertTrue(any(row['assignment_id'] == controller['id'] for row in records['value_review'].values()))

    def test_unanswered_rereads_exhaust_as_execution_incomplete_without_a_retry_command(self):
        controller, _ = self.prepare_journey(leave_pending=True)
        planned, dispatched = self.run_journey(controller)
        outcome = planned['outcome']
        self.assertEqual((outcome['kind'], outcome['origin']), ('assessment_incomplete', 'execution'))
        records = self.case.read_records()
        self.assertIn(outcome['id'], records['assessment_incomplete'])
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'incomplete')
        self.assertFalse(any(row['assignment_id'] == controller['id'] for row in records.get('value_review', {}).values()))
        self.assertFalse(any(row.get('command') == 'review-run' for row in planned['next']))
        rereads = [row for row in dispatched if row['task']['type'] == 'frame'
                   and any(key.startswith('retrieval:') for key in row['task']['obligation_ids'])]
        self.assertEqual(len(rereads), 2)
