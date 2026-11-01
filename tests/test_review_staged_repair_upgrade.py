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
    output_tokens = 128000
    timeout_seconds = 30
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

    def refuse_unsupported_repair(self, controller, context):
        before = self.case.read_records()
        value = dict(id='repaired', role=controller['role'], reviewer_id='replacement',
            route_id=controller['route_id'], dossier_id=controller['dossier_id'], author_id=controller['author_id'],
            subject=controller['subject'], context=dict(context_repair=context))
        prepared = repair.prepare_repair(before, self.case.artifacts, value)
        with self.assertRaises(ResearchError) as caught:
            self.case.assign(controller['role'], 'replacement', identifier='repaired', context_repair=context)
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        details = caught.exception.details
        self.assertTrue(details['envelope_characters'] > details['input_limit'] or
                        details['card_output_tokens'] > details['output_allowance'])
        self.assertEqual(self.case.read_records(), before)
        return prepared

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

    def test_later_predecessor_contamination_blocks_dispatch_before_reservation(self):
        controller = self.create_controller()
        successor = self.repair_controller(controller)
        planned = self.command('review-assignment', dict(controller_assignment_id=successor['id']))
        payload = planned['next'][0]['payload']
        self.case.mutate(protocol.record_context_event, dict(id='later-exposure', assignment_id=controller['id'],
            kind='contamination', source='foreign_input', reason='New foreign input reached the predecessor.',
            evidence=[self.case.supporting]))
        with patch.object(review_worker, '_entry_command', side_effect=AssertionError('Stale work must not launch')):
            with self.assertRaises(ResearchError) as rejected:
                self.command('review-run', payload)
        self.assertEqual(rejected.exception.code, 'review_context_repair_stale')
        records = self.case.read_records()
        self.assertFalse(records.get('review_reservation'))
        self.assertFalse(records.get('review_task_dispatch'))
        with self.assertRaises(ResearchError) as rejected:
            self.command('review-assignment', dict(controller_assignment_id=successor['id']))
        self.assertEqual(rejected.exception.code, 'review_context_repair_stale')

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

    def test_scale_refusal_preserves_an_accepted_contaminated_child_graph(self):
        from research_harness import direction_reviews
        controller = self.create_controller()
        self.run_frame(controller)
        planned = self.command('review-assignment', controller['next'][0]['payload'])
        adverse_worker = frontier.WORKER.replace(
            "output = {} if (root / 'refuse').exists() else build_output(body, root)",
            "output = build_output(body, root)\n"
            "    output['findings'] = [dict(id='adverse', text='The boundary case remains unsupported.',\n"
            "        adverse=True, classification=None, claim_ids=body['task']['claim_ids'],\n"
            "        grounds=[row['span'] for row in body['originals']])]")
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', adverse_worker]):
            self.command('review-run', planned['next'][0]['payload'])
        records = self.case.read_records()
        before = direction_reviews.collect_selected_task_graph(records, self.case.artifacts, controller['id'])
        attempt = max((row for row in records['review_attempt'].values() if row.get('reservation_id')),
                      key=lambda row:row['recorded_revision'])
        self.case.mutate(protocol.record_context_event, dict(id='child-exposure',
            assignment_id=attempt['assignment_id'], kind='contamination', source='foreign_input',
            reason='Foreign input reached the accepted child.', evidence=[self.case.supporting]))
        codex.probe_codex_route(self.case, 'repair-probe')
        prepared = self.refuse_unsupported_repair(controller, dict(
            assignment_id=controller['id'], event_ids=['child-exposure'], probe_id='repair-probe',
            reason='Reassess the retained child observations in a fresh context.', evidence=[self.case.supporting]))
        history = prepared['scientific_history']
        parent = next(row for row in history if row['assignment_id'] == controller['id'])
        self.assertEqual(parent['selected_observations'], before['selection']['observations'])
        self.assertEqual([row['id'] for row in parent['intermediate_findings']], ['adverse'])
        self.assertEqual(parent['independence'], 'no_longer_qualified')
        turn = next(row for row in history if row['assignment_id'] == attempt['assignment_id'])
        self.assertTrue(turn['raw_observations'])
        qualified = direction_reviews.collect_selected_task_graph(
            self.case.read_records(), self.case.artifacts, controller['id'])
        self.assertNotIn(attempt['id'], [row['id'] for row in qualified['selection']['observations']])

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

    def test_repair_preserves_unverified_capture_provenance_after_a_successful_retry(self):
        from test_review_execution_recovery import UNVERIFIED_WORKER
        controller = self.create_controller()
        planned = self.command('review-assignment', controller['next'][0]['payload'])
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', UNVERIFIED_WORKER]):
            failed = self.command('review-run', planned['next'][0]['payload'])
        retry = self.command('review-assignment', controller['next'][0]['payload'])
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            accepted = self.command('review-run', retry['next'][0]['payload'])
        successor = self.repair_controller(controller)
        history = self.packet(successor)['scientific_history']
        child = next(row for row in history if row['assignment_id'] == failed['assignment_id'])
        self.assertCountEqual([row['attempt_id'] for row in child['raw_observations']], [failed['id'], accepted['id']])
        parent = next(row for row in history if row['assignment_id'] == controller['id'])
        self.assertEqual([row['id'] for row in parent['selected_observations']], [accepted['id']])

    def test_scale_refusal_preserves_a_completed_paged_result_after_child_contamination(self):
        from test_direction_reviews_paged import PagedRecordingTests
        controller = self.create_controller()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        saved = PagedRecordingTests.run_controller(self, controller, output, 'value-review')
        records = self.case.read_records()
        child = next(row for row in records['review_assignment'].values()
                     if row.get('staged_task', {}).get('type') == 'read')
        self.case.mutate(protocol.record_context_event, dict(id='child-exposure', assignment_id=child['id'],
            kind='contamination', source='foreign_input', reason='The accepted reading context was contaminated.',
            evidence=[self.case.supporting]))
        codex.probe_codex_route(self.case, 'repair-probe')
        packet = self.refuse_unsupported_repair(controller, dict(
            assignment_id=controller['id'], event_ids=['child-exposure'], probe_id='repair-probe',
            reason='Reassess the retained paged result in a fresh context.', evidence=[self.case.supporting]))
        parent = next(row for row in packet['scientific_history'] if row['assignment_id'] == controller['id'])
        self.assertEqual(protocol._reference_templates(parent['observed_output']), protocol._reference_templates(output))
        self.assertEqual(parent['effective_binding'], saved['effective_binding'])
        self.assertEqual([row['id'] for row in packet['historical_reviews']], [saved['id']])

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
        codex.record_codex_route(self.case, 'staged-route', output_tokens=128000)

    def test_composed_adjudicator_refuses_wrong_reassessment_and_retries_the_terminal(self):
        case = self.case
        # Freeze a small frame with the real repair binding; dispatch, page
        # grounding, composition, refusal and retry use their production paths.
        payload = dict(id='focused', role='adjudicator', route_id='staged-route', dossier_id=None,
            reviewer_id='focused-third', author_id=case.read_records()['workspace']['problem']['authors'][0], context=dict(
                review_ids=['review-result-first', 'review-result-second'], objection_id='unsupported-implication'))
        prior = case.mutate(protocol.record_assignment, payload)['result']
        manifest = staged.build_manifest(dict(statement='The exact quantifier is unsupported.'),
            [dict(key='original_context', index=0, item=dict(evidence=[case.supporting]),
                  evidence_class='original_context', artifacts=[case.supporting], source_ids=[])],
            [producer.make_artifact_source(case.artifacts, case.supporting)])
        with patch.object(producer, 'build_manifest', return_value=manifest):
            controller = case.mutate(protocol.record_assignment, dict(payload, id='focused-controller',
                context=dict(staged_upgrade=dict(assignment_id=prior['id']))))['result']
        case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=controller['id'],
            kind='contamination', source='foreign_input', reason='Author context reached the focused review.',
            evidence=[case.supporting]))
        codex.probe_codex_route(case, 'repair-probe', 'staged-route')
        frame = dict(statement='The exact quantifier is unsupported.',
                     context_repair={'assignment_id': controller['id']}, historical_reviews=[])
        manifest = staged.build_manifest(frame,
            [dict(key='original_context', index=0, item=dict(evidence=[case.supporting]),
                  evidence_class='original_context', artifacts=[case.supporting], source_ids=[])],
            [producer.make_artifact_source(case.artifacts, case.supporting)])
        with patch.object(producer, 'build_manifest', return_value=manifest):
            successor = case.mutate(protocol.record_assignment, dict(payload, id='repaired', reviewer_id='replacement',
                context=dict(context_repair=dict(assignment_id=controller['id'], event_ids=['exposure'],
                    probe_id='repair-probe', reason='Reassess the exact objection in a fresh context.',
                    evidence=[case.supporting]))))['result']
        records = case.read_records()
        before = records['review_epoch'][controller['staged_review']['epoch_id']]
        after = records['review_epoch'][successor['staged_review']['epoch_id']]
        self.assertEqual(after['focused_scope'], before['focused_scope'])
        self.assertEqual(after['slot_id'], before['slot_id'])
        output = dict(objection_id='unsupported-implication', disposition='upheld', correction_admissible=None,
            reason='The original comparator does not prove the disputed implication.', evidence=[case.supporting],
            reassessment=dict(assignment_id='WRONG', findings=[]))
        (case.root / 'scientific.json').write_text(json.dumps(output))
        worker = frontier.WORKER.replace('from test_review_staged_frontier import build_output',
                                        'from test_direction_reviews_paged import build_output')
        def command(name, value):
            from research_harness import cli
            return case.mutate(cli.OPERATIONS[name], value)['result']
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
            for _ in range(35):
                planned = command('review-assignment', dict(controller_assignment_id=successor['id']))
                self.assertNotIn('outcome', planned, planned)
                next_work = planned['next'][0]
                self.assertEqual(next_work['command'], 'review-run')
                result = command('review-run', next_work['payload'])
                records = case.read_records()
                child = records['review_assignment'][result['assignment_id']]
                if child['staged_task']['type'] == 'final' and child.get('composition_plan_id'):
                    break
            else:
                self.fail('The composed adjudicator did not reach terminal acceptance')
        self.assertIn('refusal', result, result)
        self.assertEqual(result['refusal']['code'], 'review_reassessment_mismatch')
        self.assertFalse(any(row['task_id'] == child['staged_task']['id']
                             for row in records['review_task_acceptance'].values()))
        self.assertTrue(records['review_reservation'][result['reservation_id']]['released'])
        retry = command('review-assignment', dict(controller_assignment_id=successor['id']))
        self.assertEqual(retry['next'][0]['command'], 'review-run')
        self.assertEqual(retry['next'][0]['payload']['task_id'], child['staged_task']['id'])

    def test_public_adjudicator_refreeze_refuses_unsupported_scale_without_changing_slot(self):
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
        before = case.read_records()
        with self.assertRaises(ResearchError) as caught:
            case.mutate(protocol.record_assignment, dict(payload, id='repaired', reviewer_id='replacement',
                context=dict(context_repair=dict(assignment_id=controller['id'], event_ids=['exposure'],
                    probe_id='repair-probe', reason='Reassess the exact objection in a fresh context.',
                    evidence=[case.supporting]))))
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        details = caught.exception.details
        self.assertTrue(details['envelope_characters'] > details['input_limit'] or
                        details['card_output_tokens'] > details['output_allowance'])
        records = case.read_records()
        for kind in ('review_epoch', 'review_task', 'review_slot_head', 'review_assignment'):
            self.assertEqual(records.get(kind, {}), before.get(kind, {}))


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
