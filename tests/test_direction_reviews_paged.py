"""Paged recordings traverse authenticated requests and the real selected graph."""

import json
import sys
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from codex_route_fixtures import invoke_codex
from research_harness import direction_reviews, review_protocol as protocol
from research_harness import review_staged as staged, review_staged_protocol as producer, review_worker
import test_review_staged_frontier as frontier
import test_review_staged_paged_journey as paged


def build_output(body, root):
    if body['task']['type'] == 'answer':
        output = paged.build_output(body, root)
        page = output['pages'][0]
        ground = body['answer']['conclusions'][0]
        if (root / 'forge-ground').exists():
            ground = dict(ground, text='A fabricated scientific conclusion.')
        page['grounding'] = [ground]
        for fragment in page['fragments']:
            fragment['grounds'] = [ground]
        return output
    scientific = json.loads((root / 'scientific.json').read_text())
    if body['task']['type'] != 'final' or 'value' in scientific:
        return paged.build_output(body, root)
    if body['final'].get('composition_root') is None:
        return scientific  # Refuse the ordinary final's missing staged sidecars.
    decisions = {('/' + key): scientific[key]
                 for key in ('objection_id', 'disposition', 'correction_admissible')}
    checks = [dict(id=body['task']['id'] + ':' + key, decision='/' + key,
        value=scientific[key], claim_ids=[row['id'] for row in body['final']['claims']],
        reason='The delivered records resolve the disputed implication.',
        grounds=[row['id'] for row in body['final']['cells']], dependencies=[])
        for key in ('disposition', 'correction_admissible')]
    return dict(terminal=dict(objection_id=scientific['objection_id'], disposition=scientific['disposition'],
        correction_admissible=None, decisions=decisions, composition_root=body['final']['composition_root'],
        visible_conditions=body['final']['visible_conditions'], record_root=body['final']['record_root'],
        integration_id=body['final']['integration_id']), decision_checks=checks)


class PagedRecordingTests(ResultPartsCase):
    staged_entry = True
    output_tokens = 4000
    timeout_seconds = 30
    create_controller = frontier.FrontierTests.create_controller
    command = frontier.FrontierTests.command

    def run_controller(self, controller, output, command):
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        worker = frontier.WORKER.replace('from test_review_staged_frontier import build_output',
                                        'from test_direction_reviews_paged import build_output')
        next_work = dict(command='review-assignment', payload={'controller_assignment_id': controller['id']})
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
            for _ in range(35):
                if next_work['command'] == command:
                    saved = self.command(command, next_work['payload'])
                    break
                planned = self.command(next_work['command'], next_work['payload'])
                self.assertNotIn('outcome', planned, planned)
                next_work = planned['next'][0]
            else:
                self.fail('The paged controller did not reach scientific recording')
        records = self.case.read_records()
        graph = direction_reviews.collect_selected_task_graph(records, self.case.artifacts, controller['id'])
        self.assertTrue(graph['completion']['complete'], graph['completion']['issues'])
        effective = graph['effective']
        self.assertEqual(effective['kind'], 'paged')
        plan = next(row for row in records['review_composition_plan'].values()
                    if row['epoch_id'] == graph['epoch']['id'])
        self.assertNotEqual(plan['selected_observations_root'], effective['selected_observations_root'])
        self.assertEqual(effective['selected_observations_root'], graph['completion']['selected_observations_root'])
        self.assertEqual(saved['effective_binding']['selected_observations_root'], effective['selected_observations_root'])
        self.assertEqual(effective['effective_output'], output)
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'complete')
        if command == 'review-adjudication':
            state = protocol.adjudication_state(records, self.case.artifacts, saved['id'])
            self.assertTrue(state['ready'], state['obligations'])
        return saved

    def test_paged_result_records_with_the_complete_selected_graph(self):
        controller = self.create_controller()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        self.run_controller(controller, output, 'value-review')

    def test_forged_typed_page_ground_cannot_record_a_result(self):
        controller = self.create_controller()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'forge-ground').touch()
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        worker = frontier.WORKER.replace('from test_review_staged_frontier import build_output',
                                        'from test_direction_reviews_paged import build_output')
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
            for _ in range(35):
                planned = self.command('review-assignment', {'controller_assignment_id': controller['id']})
                self.assertNotIn('outcome', planned)
                next_work = planned['next'][0]
                self.assertEqual(next_work['command'], 'review-run')
                result = self.command('review-run', next_work['payload'])
                records = self.case.read_records()
                assigned = records['review_task'][next_work['payload']['task_id']]['task']
                if assigned['type'] == 'answer':
                    self.assertEqual(result['refusal']['code'], 'review_composition_conflict')
                    self.assertFalse(any(row['task_id'] == assigned['id']
                                         for row in records['review_task_acceptance'].values()))
                    break
            else:
                self.fail('The forged page did not reach scientific acceptance')
        records = self.case.read_records()
        self.assertFalse(any(row['assignment_id'] == controller['id']
                             for row in records.get('value_review', {}).values()))
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'pending')

    def test_paged_adjudication_records_with_the_complete_selected_graph(self):
        from research_harness import research_decisions
        from test_research_problem_objections import ProblemObjectionTests
        for reviewer in ('first', 'second'):
            assignment = self.case.assign('result', reviewer, identifier='source-' + reviewer)
            output = self.case.build_review_output('result')
            if reviewer == 'first':
                output['objections'] = [ProblemObjectionTests.make_objection(self.case)]
            invoke_codex(self.case, assignment['id'], output)
            self.case.mutate(research_decisions.record_value_review, dict(output,
                id='review-source-' + reviewer, dossier_id=None, assignment_id=assignment['id']))
        context = dict(review_ids=['review-source-first', 'review-source-second'],
                       objection_id='unsupported-implication')
        prior = self.case.assign('adjudicator', 'third', identifier='focused-prior', **context)
        manifest = staged.build_manifest({'objection': 'The exact quantifier is unsupported.'},
            [dict(key='original_context', index=0, item=dict(evidence=[self.case.supporting]),
                  evidence_class='original_context', artifacts=[self.case.supporting], source_ids=[])],
            [producer.make_artifact_source(self.case.artifacts, self.case.supporting)])
        with patch.object(producer, 'build_manifest', return_value=manifest):
            controller = self.case.assign('adjudicator', 'third', identifier='focused-controller',
                staged_upgrade={'assignment_id': prior['id']})
        output = dict(objection_id='unsupported-implication', disposition='upheld',
            correction_admissible=None, reason='The original comparator does not prove the disputed implication.',
            evidence=[self.case.supporting])
        self.run_controller(controller, output, 'review-adjudication')
