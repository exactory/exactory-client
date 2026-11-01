"""The operator frontier recovers a refused ordinary final as bounded answer pages."""

import json
import sys
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
import test_review_staged_frontier as fixtures
from research_harness import review_protocol, review_worker


def build_output(body, root):
    if body['task']['type'] == 'answer':
        source = body['answer']
        page = {key:source[key] for key in ('protocol', 'epoch_id', 'role', 'task_id', 'page_id',
            'revision', 'source_root', 'dependency_root', 'ownership', 'replaces')}
        ground = dict(kind='reviewer_observation', reason='The fragments were assessed against the supplied original conclusions.')
        page.update(fragments=[dict(fragment, grounds=[ground]) for fragment in source['source_fragments']],
                    grounding=[ground], findings=[], dispositions=[])
        return dict(pages=[page])
    output = fixtures.build_output(body, root)
    if body['task']['type'] == 'final':
        composition_root = body['final'].get('composition_root')
        if composition_root is None:
            # A complete scientific object with missing required sidecars is a
            # real contract refusal. It may supply a new page assessment, but
            # never receives scientific credit by itself.
            output.pop('assurance_checks')
            output.pop('decision_checks')
        else:
            output['terminal']['composition_root'] = composition_root
    return output


class PagedJourneyTests(ResultPartsCase):
    staged_entry = True
    create_controller = fixtures.FrontierTests.create_controller
    command = fixtures.FrontierTests.command

    def test_refused_final_is_planned_as_pages_and_recorded_with_effective_binding(self):
        controller = self.create_controller()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        worker = fixtures.WORKER.replace('from test_review_staged_frontier import build_output',
                                'from test_review_staged_paged_journey import build_output')
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
            for _ in range(25):
                planned = self.command('review-assignment', {'controller_assignment_id':controller['id']})
                next_work = planned['next'][0]
                if next_work['command'] == 'value-review':
                    saved = self.command('value-review', next_work['payload'])
                    break
                self.assertNotIn('outcome', planned)
                self.command('review-run', next_work['payload'])
            else:
                self.fail('Automatic answer-page recovery did not reach recording')
        records = self.case.read_records()
        state = review_protocol.assignment_state(records, self.case.artifacts, controller['id'])
        self.assertEqual(state['effective']['kind'], 'paged')
        self.assertEqual(state['output'], output)
        self.assertEqual(saved['effective_binding']['kind'], 'paged')
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'complete')
        self.assertTrue(records['review_task_refusal'])
        self.assertIn('answer', (self.case.root / 'calls').read_text().splitlines())
