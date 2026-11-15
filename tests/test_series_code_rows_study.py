"""Study refusals use reviewed records or an existing scientific workflow step."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import cli, development, review_continuation, review_series, stop_classes
from research_harness.errors import ResearchError
from research_harness.execution_evidence import validate_result_reference
from research_harness.problem_evidence import project_citations
import test_research_citation_locators as citation_fixtures
import test_review_continuation as continuation_fixtures
import test_review_parts_study as parts_fixtures
from development_fixtures import DevelopmentCase


GROUPS = Path(__file__).parent / 'fixtures/series_code_groups/d2-rows-study.json'


class StudyRowTests(unittest.TestCase):
    def prepare_fixture(self, cls):
        fixture = cls()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def take_boundary_plan(self, case, read_boundary):
        def read_plan(store, assignment_id):
            snapshot = store.snapshot()
            read_boundary(snapshot['records'])
            return snapshot, dict(state='done', chain=None, head_id=assignment_id, progress=None)
        rows = []
        with patch.object(review_series, '_read_plan', side_effect=read_plan):
            review_series.run_series(case.store, 'study-boundary', emit=rows.append)
        return rows[-1]

    def take_decision_step(self, plan, code, command):
        self.assertNotIn('unlisted', plan['details'])
        self.assertEqual((plan['state'], plan['code'], plan['stop_class']), ('stop', code, 'decision'))
        step = plan['details']['decision']['then'][0]['steps'][0]
        self.assertEqual(step['command'], command)
        return step

    def test_every_stored_state_member_ends_the_series_at_record_check(self):
        self.assertTrue(hasattr(stop_classes, '_STORED_STATE_CODES_STUDY'),
                        'Study codes need reviewed registry membership.')
        self.assert_new_readiness_support_shape_is_not_stored_state()
        self.assert_new_support_execution_reference_is_a_prerequisite()
        members = json.loads(GROUPS.read_text())['stored_state']['members']
        self.assertEqual(len(members), 21)
        fixture = self.prepare_fixture(continuation_fixtures.ContinuationTests)
        for code in members:
            with self.subTest(code=code):
                self.assertTrue(code in stop_classes.STOP_REGISTRY, code)
                def refuse(store, payload, **identity):
                    raise ResearchError(code, 'The stored study binding differs.', {'record': 'synthetic'})
                rows = []
                with patch.dict(cli.OPERATIONS, {'review-run': refuse}):
                    result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=rows.append)
                self.assertEqual(result, 3)
                terminal = rows[-1]
                self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                                 ('stop', code, 'decision'))
                self.assertNotIn('unlisted', terminal['details'])
                self.assertEqual(terminal['error']['details'], {'record': 'synthetic'})
                decision = stop_classes.describe_terminal('review_history_invalid')['details']['decision']
                decision['then'][0]['steps'] = [dict(command='record-check',
                    arguments={'--workspace': str(fixture.case.store.root)}, payload={}, chooses=[], prerequisites=[])]
                self.assertEqual(terminal['details']['decision'], decision)
        self.assertEqual(set(stop_classes._STORED_STATE_CODES_STUDY), set(members))

    def test_late_citation_locator_step_preserves_the_existing_reading(self):
        case = self.prepare_fixture(citation_fixtures.CitationLocatorTests)
        before_records = case.store.snapshot()['records']
        with self.assertRaises(ResearchError) as caught:
            project_citations(before_records, case.artifacts)
        self.assertEqual(caught.exception.code, 'problem_citation_locator_missing')
        def read_citations(records):
            return project_citations(records, case.artifacts)
        before = self.take_boundary_plan(case, read_citations)
        step = self.take_decision_step(before, caught.exception.code, 'problem-citation-locator')
        payload = case.build_payload()
        self.assertEqual(set(step['chooses']), set(payload))
        cli.OPERATIONS[step['command']](case.store, payload,
            expected_revision=case.store.revision, request_id='resolve-citation-location')
        after_records = case.store.snapshot()['records']
        self.assertEqual(project_citations(after_records, case.artifacts)[0]['locator'], case.url)
        self.assertEqual(after_records['reading'], before_records['reading'])
        self.assertEqual(after_records['source_bundle'], before_records['source_bundle'])
        after = self.take_boundary_plan(case, read_citations)
        self.assertEqual(after['state'], 'done')
        self.assertNotEqual(after, before)

    def test_current_literature_comparison_step_records_a_distinct_plan(self):
        case = self.prepare_fixture(DevelopmentCase)
        case.prepared_study()
        stale = case.plan()
        stale['literature']['foundation_digest'] = stale['literature'].pop('literature_digest')
        with self.assertRaises(ResearchError) as caught:
            case.mutate(development.plan_cycle, stale)
        self.assertEqual(caught.exception.code, 'literature_comparison_stale')
        def read_comparison(records):
            plan = records.get('cycle_plan', {}).get(stale['id'], {}).get('payload', stale)
            return development._plan_dependencies(development._Context(records, case.artifacts), plan)
        before = self.take_boundary_plan(case, read_comparison)
        step = self.take_decision_step(before, caught.exception.code, 'cycle')
        self.assertEqual(set(step['chooses']), set(case.plan()))
        self.assertNotIn('cycle-1', case.store.snapshot()['records'].get('cycle_plan', {}))
        cli.OPERATIONS[step['command']](case.store, case.plan(),
            expected_revision=case.store.revision, request_id='current-literature-plan')
        after = self.take_boundary_plan(case, read_comparison)
        self.assertIn('cycle-1', case.store.snapshot()['records']['cycle_plan'])
        self.assertEqual(after['state'], 'done')
        self.assertNotEqual(after, before)

    def test_missing_prediction_is_a_scientific_output_refusal_that_can_retry(self):
        self.assertEqual(stop_classes.STOP_REGISTRY.get('prediction_required', {}).get('next'),
                         dict(kind='outcome', outcome='scientific_refused'))
        case = self.prepare_fixture(parts_fixtures.VerificationPartsTests)
        case.enlarge_verification()
        turns, final = case.finish()
        output = case.final_output(final)
        invalid = copy.deepcopy(output)
        invalid['verdict']['prediction'] = []
        refused = case.invoke(final, invalid, 'missing-prediction')
        self.assertEqual(refused['output_refusal']['code'], 'prediction_required')
        records = case.records()
        self.assertEqual(records['review_output_refusal']['missing-prediction']['code'], 'prediction_required')
        before = review_continuation.plan_next(records, case.case.artifacts, final['id'])
        self.assertEqual((before['state'], before['command']), ('run', 'review-run'))
        case.invoke(final, output, 'corrected-prediction')
        after = review_continuation.plan_next(case.records(), case.case.artifacts, final['id'])
        self.assertNotEqual(after, before)
        self.assertEqual((after['state'], after['command']), ('handoff', 'bind-verdict'))

    def assert_new_readiness_support_shape_is_not_stored_state(self):
        case = self.prepare_fixture(DevelopmentCase)
        plan, execution, assessment = case.prepared_candidate()
        payload = case.review(execution)
        payload['checks'].append(copy.deepcopy(payload['checks'][0]))
        records = case.store.snapshot()['records']
        with self.assertRaises(ResearchError) as caught:
            development._review(development._Context(records, case.artifacts), payload, case.candidate())
        self.assertEqual(caught.exception.code, 'invalid_development')
        self.assertNotIn('invalid_development', stop_classes.STOP_REGISTRY)
        self.assertNotIn('invalid_development', stop_classes._STORED_STATE_CODES_STUDY)

    def assert_new_support_execution_reference_is_a_prerequisite(self):
        case = self.prepare_fixture(DevelopmentCase)
        artifact = case.artifacts.put(b'An observed output.', 'text/plain')
        reference = dict(kind='result', execution_id='not-recorded', output_id='result', artifact=artifact,
                         locator=dict(kind='text', quote='An observed output.'))
        with self.assertRaises(ResearchError) as caught:
            validate_result_reference(case.store.snapshot()['records'], case.artifacts, reference)
        self.assertEqual(caught.exception.code, 'unknown_execution')
        self.assertEqual(stop_classes.STOP_REGISTRY['unknown_execution']['group'], 'named_cause')
        self.assertNotIn('unknown_execution', stop_classes._STORED_STATE_CODES_STUDY)
