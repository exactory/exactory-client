"""A v3 repair step preserves accepted intermediate pages after a refusal exhausts a later page."""
import copy
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, probe_codex_route
from research_harness import bar_sources, review_parts, review_protocol, review_single_request
from research_harness.review_continuation import plan_next
from research_harness.review_failure_class import plan_after_failure
import test_review_page_answer_pointers as pointer_fixtures
import test_review_parts_slate as slate_fixtures
from test_review_parts_v3 import make_turn_output


class PartsPageHandoffTests(unittest.TestCase):
    def test_exhausted_later_parts_page_allows_step_two(self):
        journey = pointer_fixtures.PagePointerJourneyTests()
        self.addCleanup(journey.doCleanups)
        fixture = journey.set_up_fixture(slate_fixtures.SlateSeriesTests())
        journey.pin_two_pages(4)
        case = fixture.make_case()
        logical = None
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch('research_harness.review_problem_parts.preserves_ordinary_packet', return_value=False), \
                patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            current = fixture.assign(case)
            for index in range(16):
                packet = review_protocol._read_json(case.artifacts, current['packet'])
                header = packet[review_parts.KEY]
                page = header.get('page')
                if page and page['number'] == 2:
                    break
                if page:
                    logical = dict(review_parts.read_logical_packet(case.artifacts, header), review_parts=header)
                    output = case.build_slate_output(['candidate-1'])
                    candidate = logical['candidates'][0]
                    evidence = [dict(kind='record', record_kind='problem_candidate',
                                     id=candidate['id'], digest=candidate['digest'])]
                    for assurance in output['value']['assurances']:
                        assurance['evidence'] = evidence
                    _, values = journey.build_answers_by_label(logical, output)
                    answer, _ = journey.answer_page(page, journey.read_parts_labels(header), values, restart=False)
                    accepted = current['id']
                else:
                    answer = make_turn_output(packet)
                self.assertNotIn('output_refusal', invoke_codex(case, current['id'], answer))
                current = fixture.assign(case, identifier='slate-%d' % index, prior_assignment_id=current['id'])
            else:
                self.fail('The second final page was never reached.')
            self.assertIsNotNone(logical)
            for number in range(3):
                answer = dict(targets=[dict(target=handle, fields={'/foreign': 'Bad key.'}, sentence='Assess.')
                                       for handle in page['targets']])
                receipt = invoke_codex(case, current['id'], answer)
                self.assertEqual(receipt['output_refusal']['code'], 'review_composition_conflict')
                if number < 2:
                    step = plan_after_failure(case.read_records(), case.artifacts, current['id'])
                    self.assertEqual(step['state'], 'replace')
                    current = case.mutate(review_protocol.record_assignment, step['payload'])['result']
        action = plan_next(case.read_records(), case.artifacts, current['id'])
        step = action['details']['step']
        self.assertEqual(step['id'], 2)
        payload = copy.deepcopy(step['payload'])
        probe_codex_route(case, 'after-exhaustion', 'local')
        payload.update(id='repair-after-exhaustion', route_id='local')
        payload['context']['context_repair'].update(probe_id='after-exhaustion',
            reason='Reassess the preserved slot after two replacements.')
        before = case.store.revision
        recorded = case.mutate(review_protocol.record_assignment, payload)['result']
        self.assertEqual(recorded['id'], 'repair-after-exhaustion')
        self.assertGreater(case.store.revision, before)
        self.assertIn(accepted, case.read_records()['review_assignment'])
