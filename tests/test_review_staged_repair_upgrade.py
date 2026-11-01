"""Production repairs and explicit upgrades preserve the exact scientific lineage."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

import codex_route_fixtures as codex
import test_review_staged_frontier as frontier
import test_research_problem_adjudication_contract as adjudication
from problem_parts_fixtures import ResultPartsCase
from research_harness import research_decisions as decisions
from research_harness import review_context_repair as repair
from research_harness import review_protocol as protocol
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as producer
from research_harness import review_worker
from research_harness.errors import ResearchError


class StagedRepairUpgradeTests(ResultPartsCase):
    staged_entry = True
    create_controller = frontier.FrontierTests.create_controller
    command = frontier.FrontierTests.command

    def repair_controller(self, controller):
        self.case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=controller['id'],
            kind='contamination', source='foreign_input', reason='Author context reached this review.',
            evidence=[self.case.supporting]))
        codex.probe_codex_route(self.case, 'repair-probe')
        return self.case.assign(controller['role'], 'replacement', identifier='repaired', context_repair=dict(
            assignment_id=controller['id'], event_ids=['exposure'], probe_id='repair-probe',
            reason='Reassess all retained findings in a fresh context.', evidence=[self.case.supporting]))

    def run_frame(self, controller):
        planned = self.command('review-assignment', dict(controller_assignment_id=controller['id']))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            self.command('review-run', planned['next'][0]['payload'])
        return self.case.read_records()

    def test_public_repair_of_no_output_controller_uses_its_manifest(self):
        controller = self.create_controller()
        successor = self.repair_controller(controller)
        records = self.case.read_records()
        packet = self.packet(successor)
        self.assertEqual(successor['history'], [])
        self.assertEqual(repair.slot_id(records, successor), controller['slot_id'])
        self.assertEqual(packet['context_repair']['assignment_id'], controller['id'])
        self.assertEqual(packet['scientific_history'][-1]['observed_output'], None)
        self.assertEqual(successor['staged_review']['kind'], 'controller')

    def test_public_repair_retains_authenticated_staged_task_history(self):
        controller = self.create_controller()
        records = self.run_frame(controller)
        attempt = next(row for row in records['review_attempt'].values() if row.get('reservation_id'))
        raw = protocol.read_raw_observation(records, self.case.artifacts, attempt['id'])
        successor = self.repair_controller(controller)
        history = self.packet(successor)['scientific_history']
        turn = next(row for row in history if row['assignment_id'] == attempt['assignment_id'])
        self.assertEqual(turn['intermediate_output'], raw['raw_output'])
        self.assertEqual(turn['raw_observations'][0]['raw_output_digest'], raw['raw_output_digest'])
        descriptor = turn['raw_observations'][0]['request']['artifact_descriptor']
        self.assertEqual(descriptor['sha256'], attempt['request']['sha256'])
        self.assertEqual(successor['history'], [])

    def test_staged_history_with_a_changed_request_cannot_be_repaired(self):
        controller = self.create_controller()
        records = self.run_frame(controller)
        attempt = next(row for row in records['review_attempt'].values() if row.get('reservation_id'))
        capture = copy.deepcopy(records['review_observation'][attempt['reservation_id']])
        request = protocol._read_json(self.case.artifacts, attempt['request'])
        request['history'] = [dict(role='assistant', content='Unobserved scientific input.')]
        capture['observation']['request'] = protocol._put_json(self.case.artifacts, request)
        self.case.put('review_observation', capture['id'], capture)
        with self.assertRaises(ResearchError) as caught:
            self.repair_controller(controller)
        self.assertEqual(caught.exception.code, 'review_context_repair_unverified')

    def test_upgrade_inherits_recorded_reviews_in_its_slot_and_frozen_frame(self):
        original = self.assign('recorded')
        output = self.case.build_review_output('result')
        output['objections'] = [dict(id='predecessor-objection', claim='The boundary case is unsupported.',
            reason='The supporting observation covers only the interior.', evidence=[self.case.supporting],
            resolution_condition='Supply the missing boundary argument.')]
        codex.invoke_codex(self.case, original['id'], output)
        self.case.mutate(decisions.record_value_review,
            dict(output, id='original-review', dossier_id=None, assignment_id=original['id']))
        successor = self.case.assign('result', 'first', identifier='upgraded',
                                    staged_upgrade=dict(assignment_id=original['id']))
        records = self.case.read_records()
        historical = repair.collect_problem_review_history(records, successor)
        self.assertEqual([row['id'] for row in historical], ['original-review'])
        self.assertEqual(repair.slot_id(records, successor), original['slot_id'])
        frozen = records['review_epoch'][successor['staged_review']['epoch_id']]
        self.assertIn('original-review', json.dumps(frozen['manifest']['frame_fields']))
        self.assertIn('original-review', json.dumps(self.packet(successor)['historical_reviews']))
        self.assertIn('predecessor-objection', [row['id'] for row in frozen['manifest']['inherited_findings']])

    def test_upgraded_result_records_after_its_predecessor_without_duplicate(self):
        original = self.assign('recorded')
        output = self.case.build_review_output('result')
        codex.invoke_codex(self.case, original['id'], output)
        self.case.mutate(decisions.record_value_review,
            dict(output, id='original-review', dossier_id=None, assignment_id=original['id']))
        def build_small_manifest(artifacts, packet, role):
            # Retain the new historical frame, with one evidence occurrence for
            # the worker's complete, real reading/combination/final journey.
            return staged.build_manifest(dict(statement='The bounded claim.',
                historical_review_id=packet.get('historical_reviews', [{}])[0].get('id', 'missing')),
                [dict(key='assessment_evidence', index=0, item=dict(evidence=[self.case.supporting]),
                      artifacts=[self.case.supporting], source_ids=[])],
                [producer.make_artifact_source(artifacts, self.case.supporting)])

        with patch.object(producer, 'build_manifest', side_effect=build_small_manifest):
            controller = self.case.assign('result', 'first', identifier='upgraded',
                                         staged_upgrade=dict(assignment_id=original['id']))
        output = copy.deepcopy(output)
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        next_command = controller['next'][0]
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            for _ in range(40):
                result = self.command(next_command['command'], next_command['payload'])
                if next_command['command'] == 'value-review':
                    break
                self.assertTrue(result['next'], result)
                next_command = result['next'][0]
            else:
                self.fail('The upgraded operator workflow did not finish.')
        records = self.case.read_records()
        self.assertEqual({row['assignment_id'] for row in records['value_review'].values()
                          if row['payload']['stage'] == 'result'}, {original['id'], controller['id']})
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'complete')


class StagedAdjudicatorRepairTests(unittest.TestCase):
    def setUp(self):
        self.case = adjudication.ProblemAdjudicationContractTests(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.prepare_reviews(result_reviewer='first')
        codex.record_codex_route(self.case, 'staged-route')

    def test_public_staged_adjudicator_repair_preserves_focused_scope_and_slot(self):
        case = self.case
        payload = dict(id='focused', role='adjudicator', route_id='staged-route', dossier_id=None,
            reviewer_id='focused-third', author_id=case.read_records()['workspace']['problem']['authors'][0], context=dict(
                review_ids=['review-result-first', 'review-result-second'], objection_id='unsupported-implication'))
        prior = case.mutate(protocol.record_assignment, payload)['result']
        controller = case.mutate(protocol.record_assignment, dict(payload, id='focused-controller',
            context=dict(staged_upgrade=dict(assignment_id=prior['id']))))['result']
        case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=controller['id'],
            kind='contamination', source='foreign_input', reason='Author context reached the focused review.',
            evidence=[case.supporting]))
        codex.probe_codex_route(case, 'repair-probe', 'staged-route')
        successor = case.mutate(protocol.record_assignment, dict(payload, id='repaired', reviewer_id='replacement',
            context=dict(context_repair=dict(assignment_id=controller['id'], event_ids=['exposure'],
                probe_id='repair-probe', reason='Reassess the exact objection in a fresh context.',
                evidence=[case.supporting]))))['result']
        records = case.read_records()
        before = records['review_epoch'][controller['staged_review']['epoch_id']]
        after = records['review_epoch'][successor['staged_review']['epoch_id']]
        self.assertEqual(after['focused_scope'], before['focused_scope'])
        self.assertEqual(after['slot_id'], before['slot_id'])
        self.assertEqual(after['predecessor']['assignment_id'], controller['id'])
        self.assertEqual(successor['history'], [])
        child = case.mutate(protocol.record_assignment, dict(payload, id='repair-child', reviewer_id='replacement',
            context=dict(controller_assignment_id=successor['id'],
                         task_id=successor['staged_review']['task_ids'][0])))['result']
        self.assertTrue(child['prompt']['parameters']['context_repair'])
        self.assertEqual(protocol.build_assignment_request(records, case.artifacts, child)['history'], [])


class StagedVisualRepairTests(unittest.TestCase):
    def test_staged_visual_repair_uses_the_fresh_epoch_instead_of_legacy_visual_history(self):
        import test_visual_result_delivery as visual
        import test_visual_context_repair as visual_repair
        fixture = visual.VisualResultDeliveryTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.case = fixture, fixture.case
        prior = fixture.assignment
        value = {key:prior[key] for key in ('role', 'route_id', 'reviewer_id', 'author_id', 'dossier_id')}
        manifest = staged.build_manifest(dict(statement='The bounded visual claim.'), [], [])
        with patch.object(producer, 'build_manifest', return_value=manifest):
            controller = self.case.mutate(protocol.record_assignment, dict(value, id='visual-controller',
                context=dict(staged_upgrade=dict(assignment_id=prior['id']))))['result']
        evidence = fixture.artifacts.put(b'Foreign input reached the staged visual context.', 'text/plain')
        self.case.mutate(protocol.record_context_event, dict(id='visual-exposure', assignment_id=controller['id'],
            kind='contamination', source='foreign_input', reason='The staged context was contaminated.', evidence=[evidence]))
        self.prior = controller
        visual_repair.VisualContextRepairTests.probe(self, 'staged-visual-probe')
        with patch.object(producer, 'build_manifest', return_value=manifest):
            repaired = self.case.mutate(protocol.record_assignment, dict(value, id='visual-repaired',
                reviewer_id='fresh-visual-reviewer', context=dict(context_repair=dict(
                    assignment_id=controller['id'], event_ids=['visual-exposure'], probe_id='staged-visual-probe',
                    reason='Reassess the visual slot with fresh reading.', evidence=[evidence]))))['result']
        self.assertEqual(repaired['staged_review']['kind'], 'controller')
        self.assertEqual(repaired['slot_id'], controller['slot_id'])
        self.assertEqual(repaired['history'], [])


if __name__ == '__main__':
    unittest.main()
