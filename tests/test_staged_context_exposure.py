"""Context admission for new staged work and replay of already captured work."""

import json
import sys
import unittest
from unittest.mock import patch

import test_call_context_present as context
import test_review_continuation_staged as staged_fixture
import test_review_staged_frontier as frontier
from research_harness import cli, review_call_admission as admission, review_protocol as protocol
from research_harness import review_staged_protocol as staged, review_task_execution as execution, review_worker
from research_harness.errors import ResearchError


class StagedExposureTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        self.fixture = staged_fixture.StagedContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.controller = self.fixture.controller['id']
        self.fixture.execute(self.fixture.plan())
        self.dispatch = self.fixture.plan()
        self.assertEqual((self.dispatch['state'], self.dispatch['command']), ('run', 'review-run'))
        records = self.case.read_records()
        self.child = next(row['id'] for row in records['review_assignment'].values()
                          if row.get('staged_review', {}).get('task_id') == self.dispatch['payload']['task_id'])
        self.epoch = records['review_epoch'][self.dispatch['payload']['epoch_id']]

    def assert_exposure(self, identifier, code, *, counts=None):
        records = self.case.read_records()
        assignments = records['review_assignment']
        child = protocol.find_unverified_context(records, self.case.artifacts, assignments[self.child])
        controller = protocol.find_unverified_context(records, self.case.artifacts, assignments[self.controller])
        self.assertEqual(child, controller)
        self.assertEqual(controller['assignment_id'], identifier)
        if counts is not None:
            self.assertEqual(controller['count'], counts)
        plan = self.fixture.plan()
        self.assertEqual((plan['state'], plan.get('code')), ('handoff', code))
        self.assertEqual(plan['details']['turn'], controller)
        return plan

    def refuse_dispatch(self, code):
        with patch.object(review_worker, '_entry_command',
                          side_effect=AssertionError('The worker must not start.')) as worker:
            with self.assertRaises(ResearchError) as caught:
                self.fixture.execute(self.dispatch, request='blocked-dispatch')
        worker.assert_not_called()
        self.assertEqual(caught.exception.code, code)
        plan = self.fixture.plan()
        self.assertEqual(caught.exception.details['turn'], plan['details']['turn'])
        self.assertEqual(caught.exception.details['next'], plan)
        return plan

    def repair(self, plan, event='exposure'):
        step = plan['details']['step']
        self.assertEqual((step['id'], step['command']), (1, 'review-assignment'))
        payload = step['payload']
        binding = payload['context']['context_repair']
        self.assertEqual((binding['assignment_id'], binding['event_ids']), (self.controller, [event]))
        context.complete_probe(self.case, 'route', 'post-event-probe')
        payload = dict(payload, id='replacement-controller', reviewer_id='replacement-reviewer', route_id='route',
                       context=dict(context_repair=dict(binding, probe_id='post-event-probe',
                                                       reason='Reassess the exposed epoch.')))
        with patch.object(staged, 'build_manifest', return_value=self.epoch['manifest']):
            result = self.fixture.execute(dict(command=step['command'], payload=payload), request='repair')
        self.assertEqual(result['result']['context']['context_repair']['event_ids'], [event])

    def test_s1_s5_task_event_blocks_plan_and_dispatch_and_repairs_epoch(self):
        context.contaminate(self.case, self.child)
        plan = self.assert_exposure(self.child, context.CONTAMINATED)
        self.assertEqual(self.refuse_dispatch(context.CONTAMINATED), plan)
        self.assertFalse(self.case.read_records().get('review_reservation'))
        self.repair(plan)

    def test_s2_s5_predecessor_event_blocks_plan_and_dispatch_and_appears_in_status(self):
        context.contaminate(self.case, 'prior')
        plan = self.assert_exposure('prior', context.CONTAMINATED)
        self.assertEqual(self.refuse_dispatch(context.CONTAMINATED), plan)
        self.assertFalse(self.case.read_records().get('review_reservation'))
        rows = cli._build_status_report(self.case.store)['obligations']
        row, = [row for row in rows if row['code'] == context.CONTAMINATED
                and row.get('assignment_id', row.get('controller_assignment_id')) == self.controller]
        self.assertEqual(row['turn'], plan['details']['turn'])
        self.repair(plan)

    def test_s3_event_after_child_check_blocks_serialized_reservation(self):
        reserve = execution.reserve_task

        def event_before_reservation(*args, **kwargs):
            context.contaminate(self.case, self.controller)
            return reserve(*args, **kwargs)

        with patch.object(execution, 'reserve_task', side_effect=event_before_reservation):
            plan = self.refuse_dispatch(context.CONTAMINATED)
        self.assertEqual(plan['details']['turn']['assignment_id'], self.controller)
        self.assertFalse(self.case.read_records().get('review_reservation'))
        self.repair(plan)

    def test_s3_event_after_reservation_blocks_claim_and_releases_capacity(self):
        claim = review_worker._claim_launch

        def event_before_claim(*args, **kwargs):
            context.contaminate(self.case, self.controller)
            return claim(*args, **kwargs)

        with patch.object(review_worker, '_claim_launch', side_effect=event_before_claim):
            plan = self.refuse_dispatch(context.CONTAMINATED)
        reservation, = self.case.read_records()['review_reservation'].values()
        self.assertTrue(reservation['released'])
        self.assertIsNone(reservation['launch'])
        self.assertEqual(plan['details']['turn']['assignment_id'], self.controller)
        self.repair(plan)

    def test_s4_s5_waiting_dispatch_rereads_route_proof(self):
        original_wait = admission.wait_for_admission

        def wait(store, attempt):
            with patch.object(admission, 'wait_for_admission', original_wait):
                context.fail_probe(self.case, 'route')
            return attempt(True)

        with patch.object(admission, 'wait_for_admission', side_effect=wait):
            plan = self.refuse_dispatch(context.UNVERIFIED)
        self.assertEqual(plan, self.assert_exposure('bar-first', context.UNVERIFIED, counts=4))
        self.assertFalse(self.case.read_records().get('review_reservation'))
        step = plan['details']['step']
        self.assertEqual(step['command'], 'review-probe')
        context.complete_probe(self.case, step['payload']['route_id'])
        self.assertEqual(self.fixture.plan()['state'], 'run')

    def test_s7_capture_is_accepted_after_predecessor_event(self):
        """Contract. Acceptance of a captured answer passes on the base."""
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]) as worker:
            with patch.object(execution, 'accept_observation', side_effect=RuntimeError('acceptance interrupted')):
                with self.assertRaisesRegex(RuntimeError, 'acceptance interrupted'):
                    self.fixture.execute(self.dispatch, request='captured-call')
            context.contaminate(self.case, 'prior')
            plan = self.fixture.plan()
            self.assertEqual((plan['state'], plan['payload']['action'], plan['request_id']),
                             ('resume', 'accept', 'captured-call'))
            self.fixture.execute(plan)
        worker.assert_called_once()
        self.assertEqual(len(self.case.read_records()['review_task_acceptance']), 1)


class StagedAdjudicatorExposureTests(context.RoleFixtures, unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_s6_staged_adjudicator_tasks_share_the_controller_exposure(self):
        """Write two task assignment rows with prepare_child's lineage members for the staged adjudicator."""
        case, head = self.build_adjudicator()
        records = case.read_records()
        assignments = records['review_assignment']
        controller = dict(assignments[head], staged_review=dict(
            protocol='fixture-staged', kind='controller', epoch_id='focused-epoch'))
        case.put('review_assignment', head, controller)
        for identifier in ('left-task', 'right-task'):
            binding = dict(protocol='fixture-staged', kind='task', controller_assignment_id=head,
                           epoch_id='focused-epoch', task_id=identifier + '-plan')
            child = dict(controller, id=identifier, staged_review=binding,
                         context=dict(controller_assignment_id=head, task_id=binding['task_id']),
                         history=[], history_assignment_id=None, session_id=identifier,
                         recorded_revision=case.store.revision + 1)
            case.put('review_assignment', identifier, child)
        context.contaminate(case, 'left-task')
        records = case.read_records()
        for identifier in (head, 'right-task'):
            with self.subTest(assignment=identifier):
                lost = protocol.find_unverified_context(records, case.artifacts,
                                                        records['review_assignment'][identifier])
                self.assertIsNotNone(lost)
                self.assertEqual((lost['assignment_id'], lost['events']), ('left-task', ['exposure']))
