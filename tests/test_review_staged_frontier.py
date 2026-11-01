"""Operator commands own the staged frontier and its bounded failure transitions."""

import copy
import json
import sys
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import cli, review_protocol as protocol, review_staged as staged
from research_harness import review_staged_protocol as producer, review_worker
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, profile


WORKER = '''
import sys
from pathlib import Path
from research_harness import review_worker, review_transport, review_codex_transport, review_excerpts
from codex_route_fixtures import build_codex_response
from test_review_staged_frontier import build_output
review_codex_transport.runtime = lambda: {'adapter':'codex_cli_v1','fixture':'reviewer-excerpt-test'}
review_codex_transport.NATIVE_DECLARATIONS = ()
review_codex_transport.NATIVE_DECLARATION_SETS = {()}
def send(route, request, credential):
    root = Path(sys.argv[1])
    body = review_excerpts.read_sent_packet(request)['staged_review']
    with (root / 'calls').open('a') as stream: stream.write(body['task']['type'] + '\\n')
    output = {} if (root / 'refuse').exists() else build_output(body, root)
    return build_codex_response(route, request, output)
review_transport.send_request = send
review_worker.run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]))
'''


def build_output(body, root):
    task = body['task']
    prefix = task['id']
    if task['type'] == 'frame':
        return dict(claims=[dict(id='frame-record:' + digest(row['span']), kind='record', spans=[row['span']],
                    dependencies=[], children=[]) for row in body['frame']['fields']],
                    conditions=[], dependencies=[], unresolved=[])
    if task['type'] == 'read':
        cards = []
        for unit in body['units']:
            spans = [row['span'] for row in body['originals']]
            cells = [dict(id=prefix + ':' + claim['id'], occurrence_id=unit['id'], claim_id=claim['id'],
                status='supported', reason='The original supports the assigned claim.', grounds=spans,
                scope='joint_original', context_root=claim['context_root']) for claim in body['claims']]
            cards.append(dict(id=prefix + ':card', occurrence_id=unit['id'], kind='full',
                establishes='The source supports the claim.', evidence_type='assertion',
                conclusions=[dict(id=prefix + ':conclusion', text='The claim holds.', grounds=spans)],
                conditions=[], judgments=cells, critical_spans=spans,
                findings=[], unresolved=[], dependencies=[], fragment_ids=[]))
        return dict(cards=cards)
    if task['type'] == 'combine':
        return dict(combinations=[dict(id=prefix + ':combination', task_id=prefix,
            card_ids=task['card_ids'], scope=task['scope'], synthesis='The cards agree on the stated scope.',
            selected_groups=[], segment_task_ids=[], reconciliation=None)])
    if task['type'] == 'integrate':
        receipt = dict(body['integrate']['copy'], id=prefix + ':integration',
            dispositions=[dict(id=key, disposition='retained', reason='Retained on the original scope.',
                grounds=[key]) for key in body['integrate']['copy']['obligation_ids']],
            findings=[], answers=[], selected_groups=[], retrievals=[], dependencies=[], unresolved=[],
            decision_constraints={})
        return dict(integrations=[receipt])
    if task['type'] == 'final':
        output = json.loads((root / 'scientific.json').read_text())
        checks = [dict(id=prefix + ':' + kind, assurance=kind, status='passed',
            claim_ids=[row['id'] for row in body['final']['claims']], reason='The grounded scope was assessed.',
            grounds=[row['id'] for row in body['final']['cells']], conditions_satisfied=True, dependencies=[])
            for kind in staged.ASSURANCES]
        scientific = copy.deepcopy(output)
        scientific['value']['assurances'] = {row['kind']:row for row in output['value']['assurances']}
        decisions = {}
        for path in body['decisions']:
            value = scientific
            for key in path.split('/')[1:]:
                value = value[key]
            decisions[path] = value
        decision_checks = [dict(id=prefix + ':decision:' + path, decision=path, value=value,
            claim_ids=[row['id'] for row in body['final']['claims']], reason='The original scope supports this choice.',
            grounds=[row['id'] for row in body['final']['cells']], dependencies=[])
            for path, value in decisions.items()]
        output.update(terminal=dict(decisions=decisions, assurances={kind:'passed' for kind in staged.ASSURANCES},
            visible_conditions=body['final']['visible_conditions'], record_root=body['final']['record_root'],
            integration_id=body['final']['integration_id']), assurance_checks=checks, decision_checks=decision_checks)
        return output
    raise AssertionError(task['type'])


class FrontierTests(ResultPartsCase):
    staged_entry = True
    output_tokens = 4000
    timeout_seconds = 30

    def create_controller(self):
        # Keep the frozen scientific fixture small; inventory construction itself
        # is covered by the manifest and protocol tests. All dispatch/validation
        # and recording below use the real operator command implementations.
        manifest = staged.build_manifest({'statement':'The bounded claim.', 'grade':2},
            [dict(key='assessment_evidence', index=0, item=dict(evidence=[self.case.supporting]),
                  artifacts=[self.case.supporting], source_ids=[])],
            [producer.make_artifact_source(self.case.artifacts, self.case.supporting)])
        prior = self.assign('prior')
        with patch.object(producer, 'build_manifest', return_value=manifest):
            return self.case.assign('result', 'first', identifier='controller',
                                    staged_upgrade={'assignment_id':prior['id']})

    def command(self, name, payload):
        return cli.OPERATIONS[name](self.case.store, payload, expected_revision=self.case.store.revision,
                                   request_id='operator-' + str(self.case.store.revision))['result']

    def test_commands_plan_run_and_record_the_effective_result(self):
        controller = self.create_controller()
        self.assertEqual(controller['next'], [producer.describe_planning_command(controller['id'])])
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        task_types = []
        next_command = controller['next'][0]
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            for _ in range(20):
                self.assertIn(next_command['command'], ('review-assignment', 'review-run', 'value-review'))
                if next_command['command'] == 'value-review':
                    self.command('value-review', next_command['payload'])
                    break
                if next_command['command'] == 'review-run':
                    task_id = next_command['payload']['task_id']
                    task_types.append(self.case.read_records()['review_task'][task_id]['task']['type'])
                result = self.command(next_command['command'], next_command['payload'])
                self.assertTrue(result['next'])
                next_command = result['next'][0]
            else:
                self.fail('The operator frontier did not terminate')
        records = self.case.read_records()
        state = protocol.assignment_state(records, self.case.artifacts, controller['id'])
        self.assertEqual(state['effective']['effective_output'], output)
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'complete')
        self.assertTrue(any(row['assignment_id'] == controller['id'] for row in records['value_review'].values()))
        self.assertEqual(task_types, ['frame', 'read', 'combine', 'integrate', 'final'])

    def test_refused_capture_retries_identical_request_then_exhausts_as_recorded_outcome(self):
        controller = self.create_controller()
        (self.case.root / 'refuse').touch()
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            for _ in range(50):
                planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
                if planned.get('outcome'):
                    break
                self.command('review-run', planned['next'][0]['payload'])
            else:
                self.fail('Exhausted work never recorded an incomplete outcome')
        records = self.case.read_records()
        outcome = planned['outcome']
        self.assertEqual((outcome['kind'], outcome['origin']), ('assessment_incomplete', 'execution'))
        self.assertIn(outcome['id'], records['assessment_incomplete'])
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'incomplete')
        first = controller['staged_review']['task_ids'][0]
        captures = [row for row in records['review_observation'].values() if row['task_id'] == first]
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0]['observation']['request'], captures[1]['observation']['request'])
        self.assertEqual(len([row for row in records['review_task_refusal'].values() if row['task_id'] == first]), 2)
        self.assertTrue(records['review_task_repair'])
        self.assertFalse(any(row.get('command') == 'review-run' for row in planned['next']))

    def test_packet_reports_output_tokens_separately_from_characters(self):
        controller = self.create_controller()
        records = self.case.read_records()
        planned = records['review_task'][controller['staged_review']['task_ids'][0]]
        body = json.loads(self.case.artifacts.read(planned['packet']))['staged_review']
        measurement = planned['task']['measurement']
        self.assertEqual(measurement['configured_output_tokens'],
                         records['review_route']['route']['configuration']['max_output_tokens'])
        self.assertEqual(body.get('output_tokens'),
                         measurement['configured_output_tokens'] - measurement['output_margin_tokens'])
        self.assertNotIn('output_characters', body)
        self.assertIn('output_tokens', planned['prompt']['text'])
        self.assertNotIn('output_characters', planned['prompt']['text'])

    def test_frame_sizing_uses_joint_profile_instead_of_output_token_character_cut(self):
        records = self.case.read_records()
        controller = self.assign('sizing')
        route = copy.deepcopy(records['review_route']['route'])
        route['configuration']['timeout_seconds'] = 600
        records['review_route']['route'] = route
        value = epoch(route_id='route', manifest=staged.build_manifest({'statement':'x' * 2000}, [], []))
        calibrated = profile(output_tokens_per_character=4, output_base_tokens=10, output_margin_tokens=10,
                             seconds_per_character=1, duration_limit_seconds=100)
        with patch.object(producer, 'build_profiles', return_value={'frame':calibrated}):
            planned = producer.prepare_frame_tasks(records, self.case.artifacts, controller, value, route)
        self.assertGreater(len(planned), 1)
        for row in planned:
            measured = row['task']['measurement']
            self.assertLessEqual(measured['duration_seconds'], 100)
            self.assertLessEqual(sum(span['end']-span['start'] for span in row['task']['inputs']), 99)


    def test_render_overflow_subdivides_before_execution_incompleteness(self):
        from research_harness.errors import ResearchError
        controller = self.create_controller()
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
            self.command('review-run', planned['next'][0]['payload'])
        original = producer.build_request
        def constrained(artifacts, route, prompt, packet):
            body = packet['staged_review']
            if body['task']['type'] == 'read' and sum(len(row['text']) for row in body['originals']) > 100:
                raise ResearchError('review_task_pending', 'The actual rendered originals exceed this fixture allowance')
            return original(artifacts, route, prompt, packet)
        with patch.object(producer, 'build_request', side_effect=constrained):
            planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
        self.assertNotIn('outcome', planned)
        records = self.case.read_records()
        self.assertTrue(records['review_task_repair'])
        task = records['review_task'][planned['next'][0]['payload']['task_id']]['task']
        self.assertLessEqual(sum(row['end']-row['start'] for row in task['inputs']), 100)

    def test_a_refused_capture_can_recover_without_resampling_an_accepted_output(self):
        controller = self.create_controller()
        refused = self.case.root / 'refuse'
        refused.touch()
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            first = self.command('review-assignment', {'controller_assignment_id':controller['id']})
            failure = self.command('review-run', first['next'][0]['payload'])
            self.assertTrue(failure['refusal'])
            refused.unlink()
            second = self.command('review-assignment', {'controller_assignment_id':controller['id']})
            self.assertEqual(first['task_ids'], second['task_ids'])
            accepted = self.command('review-run', second['next'][0]['payload'])
            self.assertGreater(accepted['usage']['wall_seconds'], 0)
            replay = self.command('review-run', second['next'][0]['payload'])
            self.assertEqual(accepted['id'], replay['id'])
        self.assertEqual(len((self.case.root / 'calls').read_text().splitlines()), 2)


    def test_failed_transport_returns_the_exact_bounded_retry_command(self):
        controller = self.create_controller()
        planned = self.command('review-assignment', controller['next'][0]['payload'])
        failed_worker = WORKER.replace("output = {} if (root / 'refuse').exists() else build_output(body, root)",
                                     "raise RuntimeError('fixture transport failed')")
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', failed_worker]):
            failed = self.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['next'], controller['next'])
        retried = self.command(failed['next'][0]['command'], failed['next'][0]['payload'])
        self.assertEqual(retried['next'][0]['payload']['task_id'], planned['next'][0]['payload']['task_id'])

    def test_accepted_capture_with_interrupted_release_returns_reconciliation_then_the_next_task(self):
        controller = self.create_controller()
        first = self.command('review-assignment', controller['next'][0]['payload'])
        def join_without_releasing(store, reservation_id, process, request_id):
            if process is not None:
                process.wait(timeout=10)
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]), \
                patch.object(producer, 'finish_worker', side_effect=join_without_releasing):
            captured = self.command('review-run', first['next'][0]['payload'])
        planned = self.command(captured['next'][0]['command'], captured['next'][0]['payload'])
        reconcile = planned['next'][0]
        self.assertEqual(reconcile['payload'].get('action'), 'reconcile')
        self.assertEqual(reconcile['payload']['task_id'], first['next'][0]['payload']['task_id'])
        reconciled = self.command(reconcile['command'], reconcile['payload'])
        self.assertEqual(reconciled['next'], controller['next'])
        self.assertTrue(reconciled['released'])
        ready = self.command(reconciled['next'][0]['command'], reconciled['next'][0]['payload'])
        self.assertNotEqual(ready['next'][0]['payload']['task_id'], first['next'][0]['payload']['task_id'])

    def test_invalid_scientific_final_is_refused_before_acceptance(self):
        controller = self.create_controller()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed', reason=None)
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            for _ in range(5):
                planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
                result = self.command('review-run', planned['next'][0]['payload'])
        self.assertIn('refusal', result)
        records = self.case.read_records()
        self.assertFalse(any(records['review_task'][row['task_id']]['task']['type'] == 'final'
                             for row in records.get('review_task_acceptance', {}).values()))

    def test_elapsed_budget_records_an_incomplete_outcome_before_more_calls(self):
        controller = self.create_controller()
        self.assertIn('staged_started_at', controller)
        with patch('time.time', return_value=controller['staged_started_at'] + 86401):
            planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
        self.assertEqual(planned['outcome']['origin'], 'execution')
        self.assertEqual(self.case.read_records().get('review_reservation', {}), {})

    def test_an_unrenderable_minimal_frame_is_recorded_incomplete_at_creation(self):
        with patch.object(producer, 'read_input_limit', return_value=1000):
            controller = self.create_controller()
        records = self.case.read_records()
        head = records['review_slot_head'][controller['slot_id']]
        self.assertEqual(head['state'], 'incomplete')
        outcome = records['assessment_incomplete'][head['outcome']['id']]
        self.assertEqual(outcome['origin'], 'execution')
        for key in outcome['origin_binding']['execution_receipts']:
            self.assertIn(key, records['review_task_refusal'])

    def test_completed_frontier_cannot_start_a_new_task_after_the_epoch_call_cap(self):
        original = producer.build_epoch
        def bounded(*args, **kwargs):
            value = original(*args, **kwargs)
            spec = {key:item for key,item in value.items() if key not in ('digest', 'material_objection_entries')}
            spec['budgets'] = dict(spec['budgets'], max_calls=1)
            return staged.freeze_epoch(spec)
        with patch.object(producer, 'build_epoch', side_effect=bounded):
            controller = self.create_controller()
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
            self.command('review-run', planned['next'][0]['payload'])
            planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
        self.assertEqual(planned['outcome']['origin'], 'execution')
        self.assertEqual(len(self.case.read_records()['review_reservation']), 1)
