"""Decision-record boundaries use reviewed rows and accept their named commands."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from research_harness import cli, review_continuation, review_series, stop_classes
from research_harness.errors import ResearchError
import test_review_continuation as continuation_fixtures


ROOT = Path(__file__).resolve().parents[1]
GROUPS = ROOT / 'tests/fixtures/series_code_groups/d2-rows-decision.json'
COMMANDS = {
    'configuration_missing': 'init',
    'constitution_revalidation_required': 'constitution',
    'grand_challenge_missing': 'grand-challenge',
    'problem_attempt_superseded': 'problem-attempt',
    'problem_attempt_unbound': 'search deliver-attempt',
    'problem_citation_evidence_unbound': 'problem-citation-metadata',
    'problem_citation_locator_unbound': 'problem-citation-locator',
    'problem_objections_refresh_required': 'problem-objections-carry',
    'problem_result_extension_unexplained': 'problem-value',
    'problem_result_missing': 'problem-value',
    'problem_result_superseded': 'problem-value',
    'problem_result_unbound': 'search deliver-attempt',
    'problem_value_reading_missing': 'read',
    'problem_value_stale': 'problem-value',
    'resource_budget_exhausted': 'budget',
    'round_exemplar_missing': 'require-fulltext',
    'round_search_missing': 'search',
}


class DecisionRowTests(unittest.TestCase):
    def prepare_fixture(self, fixture_type):
        fixture = fixture_type()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def take_boundary_plan(self, case, check):
        """Run the real prerequisite check at the planner's head-reading boundary.

        Only the unrelated review head is stubbed. The check reads this store,
        the row comes through plan_next, and the repair uses its real command.
        """
        def find_head(records, artifacts, head_id, *, context_failures=None):
            check(records, artifacts)
            return dict(id=head_id, role='bar', context={})

        with patch.object(review_continuation, '_find_head', side_effect=find_head), patch.object(
                review_continuation, '_describe_chain', return_value='session'), patch.object(
                review_continuation, '_describe_progress', return_value={}), patch(
                'research_harness.problem_delivery.find_review_post', return_value=None), patch.object(
                review_continuation, '_plan_head', return_value=dict(state='done')):
            return review_continuation.plan_next(case.store.snapshot()['records'], case.artifacts, 'head')

    def require_obligation(self, pending, code):
        item = next((row for row in pending if row['code'] == code), None)
        if item is not None:
            raise ResearchError(code, item['explanation'], item)

    def execute_step(self, case, plan, choices):
        code = plan['code']
        self.assertNotIn('unlisted', plan['details'])
        step = plan['details']['decision']['then'][0]['steps'][0]
        self.assertEqual(step['command'], COMMANDS[code])
        self.assertEqual(set(choices), set(step['chooses']))
        payload = dict(step['payload'], **choices)
        return case.mutate(cli.OPERATIONS[step['command']], payload)['result']

    def test_each_stored_state_member_ends_with_the_group_record_check_decision(self):
        fixture = self.prepare_fixture(continuation_fixtures.ContinuationTests)
        codes = getattr(stop_classes, '_STORED_STATE_CODES_DECISION', ())
        self.assertEqual(len(codes), 17)
        self.assertEqual(set(codes), set(json.loads(GROUPS.read_text())['stored_state']['members']))
        for code in codes:
            with self.subTest(code=code):
                def refuse(store, payload, **identity):
                    raise ResearchError(code, 'The retained binding differs.', {'binding': 'retained'})
                rows = []
                with patch.dict(cli.OPERATIONS, {'review-run': refuse}):
                    status = review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append)
                last = rows[-1]
                self.assertEqual((status, last['state'], last['code'], last['stop_class']),
                                 (3, 'stop', code, 'decision'))
                self.assertNotIn('unlisted', last['details'])
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], 'stored_state')
                decision = stop_classes.describe_terminal('review_history_invalid')['details']['decision']
                decision['then'][0]['steps'] = [dict(command='record-check',
                    arguments={'--workspace': str(fixture.case.store.root)}, payload={}, chooses=[], prerequisites=[])]
                self.assertEqual(last['details']['decision'], decision)
                self.assertEqual(last['error']['details'], {'binding': 'retained'})
        self.assertEqual(fixture.case.read_records().get('review_attempt', {}), {})

    def test_prerequisite_rows_keep_the_named_command_and_its_payload_rule(self):
        for code, command in COMMANDS.items():
            with self.subTest(code=code):
                plan = stop_classes.describe_terminal(code)
                self.assertNotIn('unlisted', plan['details'])
                self.assertEqual((plan['state'], plan['stop_class']), ('stop', 'decision'))
                step = plan['details']['decision']['then'][0]['steps'][0]
                self.assertEqual(step['command'], command)
                self.assertNotIn('assignment_id', step['payload'])
                self.assertTrue(step['chooses'])

    def test_invalid_strategy_output_records_its_refusal_then_accepts_the_retry(self):
        fixture = self.prepare_fixture(continuation_fixtures.ContinuationTests)
        self.assertEqual(stop_classes.STOP_REGISTRY.get('invalid_strategy', {}).get('next'),
                         dict(kind='outcome', outcome='scientific_refused'))
        invalid = fixture.case.build_review_output('bar')
        invalid['value']['evidence'] = [{'unexpected': 'evidence'}]
        fixture.invoke(invalid)
        before = fixture.plan()
        with self.assertRaises(ResearchError) as caught:
            fixture.execute(before, request='refuse-evidence')
        self.assertEqual(caught.exception.code, 'invalid_strategy')
        refusal = fixture.case.read_records()['review_output_refusal']['observed']
        self.assertEqual((refusal['code'], refusal['check']), ('invalid_strategy', 'evidence.shape'))
        after = fixture.plan()
        self.assertEqual((after['state'], after['command']), ('replace', 'review-assignment'))
        self.assertNotEqual(after, before)
        fixture.execute(after, request='replace-refused-output')
        retry = fixture.plan()
        self.assertEqual((retry['state'], retry['command']), ('run', 'review-run'))
        with patch('research_harness.review_transport.send_request', return_value=
                   fixture.case.build_provider_response(fixture.case.build_review_output('bar'))):
            fixture.execute(retry)
        fixture.execute(fixture.plan(), request='record-retry')
        self.assertEqual(fixture.plan()['state'], 'done')

    def test_invalid_rubric_output_records_its_refusal_then_accepts_the_retry(self):
        from test_research_problem_reviews import ProblemReviewTests
        case = self.prepare_fixture(ProblemReviewTests)
        self.assertEqual(stop_classes.STOP_REGISTRY['invalid_rubric_grades']['next'],
                         dict(kind='outcome', outcome='scientific_refused'))
        case.observe_bar_reviews()
        invalid = case.build_review_output('result')
        invalid['value']['rubric']['priority'] = 4
        payload = case.observe('result', 'first', output=invalid, record=False)
        before = review_continuation.plan_next(case.read_records(), case.artifacts, payload['assignment_id'])
        with self.assertRaises(ResearchError) as caught:
            case.mutate(cli.OPERATIONS['value-review'], payload)
        self.assertEqual(caught.exception.code, 'invalid_rubric_grades')
        refusal = case.read_records()['review_output_refusal']['attempt-result-first']
        self.assertEqual((refusal['code'], refusal['check']), ('invalid_rubric_grades', 'review.value'))
        after = review_continuation.plan_next(case.read_records(), case.artifacts, payload['assignment_id'])
        self.assertEqual((after['state'], after['command']), ('replace', 'review-assignment'))
        self.assertNotEqual(after, before)
        case.mutate(cli.OPERATIONS[after['command']], after['payload'])
        retry = review_continuation.plan_next(case.read_records(), case.artifacts, payload['assignment_id'])
        self.assertEqual((retry['state'], retry['command']), ('run', 'review-run'))
        with patch('research_harness.review_transport.send_request', return_value=
                   case.build_provider_response(case.build_review_output('result'))):
            case.mutate(cli.OPERATIONS[retry['command']], retry['payload'])
        current = review_continuation.plan_next(case.read_records(), case.artifacts, payload['assignment_id'])
        case.mutate(cli.OPERATIONS[current['command']], current['payload'])
        self.assertEqual(review_continuation.plan_next(case.read_records(), case.artifacts,
                                                       payload['assignment_id'])['state'], 'done')

    def test_missing_configuration_accepts_the_named_initialization(self):
        from literature_fixtures import LiteratureCase
        from research_harness import principles
        case = self.prepare_fixture(LiteratureCase)
        def check(records, artifacts):
            self.require_obligation(principles.configuration_state(records, artifacts, 'research')['obligations'],
                                    'configuration_missing')
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, dict(profile='research', target=None))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')

    def test_constitution_step_keeps_the_adopted_policy_bytes(self):
        from literature_fixtures import LiteratureCase
        from research_harness import principles
        case = self.prepare_fixture(LiteratureCase)
        old_path = case.root / 'earlier-policy.md'
        old_path.write_text('# Constitution\n\nVersion: 5\n\nEarlier rules.\n')
        with patch.object(principles, 'CONSTITUTION_PATH', old_path):
            first = case.mutate(principles.initialize_research, dict(profile='research', target=None))['result']
        old = first['constitution']
        def check(records, artifacts):
            self.require_obligation(principles.configuration_state(records, artifacts, 'research')['obligations'],
                                    'constitution_revalidation_required')
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, dict(previous_sha256=old['sha256'], reason='Adopt the current policy.'))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        archive = case.store.snapshot()['records']['constitution'][old['sha256']]
        self.assertEqual(case.artifacts.read(archive['artifact']), old_path.read_bytes())

    def test_grand_challenge_step_records_the_missing_study_record(self):
        from test_research_synthesis import SynthesisCase
        from research_harness import synthesis
        case = self.prepare_fixture(SynthesisCase)
        case.configured()
        link = case.read_source()
        def check(records, artifacts):
            self.require_obligation(synthesis.synthesis_state(records, artifacts, 'research')['obligations'],
                                    'grand_challenge_missing')
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, case.grand_challenge(link))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')

    def test_budget_step_uses_the_account_budget_and_keeps_its_charges(self):
        from test_research_resources import ResourceTests, limits
        from research_harness import resources
        case = self.prepare_fixture(ResourceTests)
        case.budget(readings=0)
        def check(records, artifacts):
            self.require_obligation(resources.obligations(records, 'research'), 'resource_budget_exhausted')
        before = self.take_boundary_plan(case, check)
        charges = copy.deepcopy(case.store.snapshot()['records'].get('resource_account', {}))
        self.execute_step(case, before, dict(profile='research', purpose='literature', limits=limits(readings=2),
                                            reason='The owner grants two readings.'))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertEqual(case.store.snapshot()['records'].get('resource_account', {}), charges)

    def test_superseded_attempt_step_binds_the_current_snapshot_and_preserves_the_previous_attempt(self):
        from test_research_problem_attempt import ProblemAttemptTests, build_native_attempt, put_native_attempt
        from research_harness import problem_attempt
        case = self.prepare_fixture(ProblemAttemptTests)
        first = case.record_attempt(case.solver_payload())
        native = build_native_attempt(case.statement['statement'], [], {})
        native['totals']['moves'] -= 1
        latest = put_native_attempt(case, native)
        replacement = copy.deepcopy(first)
        replacement.pop('recorded_revision')
        replacement.update(id='current-attempt', solver=dict(first['solver'], native_attempt_digest=latest))
        def check(records, artifacts):
            problem_attempt.validate_attempt(records, artifacts, problem_attempt.find_attempt(records))
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, replacement)
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertEqual(case.store.snapshot()['records']['problem_attempt'][first['id']], first)

    def test_result_steps_record_a_replacement_value_and_preserve_earlier_bytes(self):
        from test_research_problem_objective_results import (ProblemObjectiveResultTests, build_objective_native,
                                                            build_objective_result)
        from test_research_problem_attempt import put_native_attempt
        from research_harness import problem_evidence
        for code in ('problem_result_extension_unexplained', 'problem_result_missing', 'problem_result_superseded'):
            with self.subTest(code=code):
                case = self.prepare_fixture(ProblemObjectiveResultTests)
                native = build_objective_native()
                if code == 'problem_result_extension_unexplained':
                    native['totals']['moves'] += 1
                reference = build_objective_result(put_native_attempt(case, native))
                valid = dict(reference)
                if code == 'problem_result_extension_unexplained':
                    valid['extension_reason'] = 'One further check addresses the boundary.'
                if code == 'problem_result_missing':
                    reference['acceptance_id'] = 'not-an-acceptance'
                if code == 'problem_result_superseded':
                    native['totals']['moves'] -= 1
                    valid['native_attempt_digest'] = put_native_attempt(case, native)
                def check(records, artifacts):
                    value = records.get('problem_value', {}).get('current-value')
                    item = reference if value is None else value['accounts']['explanatory_use'][0]['evidence'][0]
                    problem_evidence.validate_result(records, item)
                before = self.take_boundary_plan(case, check)
                retained = copy.deepcopy(case.store.snapshot()['records']['native_attempt'])
                self.execute_step(case, before, case.build_value([valid], 'current-value'))
                self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
                self.assertEqual(case.store.snapshot()['records']['native_attempt'], retained)
                case.doCleanups()

    def test_value_currency_step_accounts_for_a_later_delivered_snapshot(self):
        from test_research_problem_objective_results import (ProblemObjectiveResultTests, build_objective_native,
                                                            build_objective_result)
        from test_research_problem_attempt import put_native_attempt
        from research_harness.problem_account_derivations import assess_account_currency
        from research_harness.problem_claims import find_value
        case = self.prepare_fixture(ProblemObjectiveResultTests)
        native = build_objective_native()
        reference = build_objective_result(put_native_attempt(case, native))
        old = case.mutate(cli.OPERATIONS['problem-value'], case.build_value([reference]))['result']
        native['totals']['moves'] -= 1
        reference['native_attempt_digest'] = put_native_attempt(case, native)
        def check(records, artifacts):
            self.require_obligation(assess_account_currency(records, find_value(records)), 'problem_value_stale')
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, case.build_value([reference], 'current-value'))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertEqual(case.store.snapshot()['records']['problem_value'][old['id']], old)

    def test_citation_metadata_step_replaces_a_changed_provenance_binding(self):
        from test_research_problem_citation_metadata import CitationMetadataCase
        from research_harness import problem_citation_metadata as metadata
        case = self.prepare_fixture(CitationMetadataCase)
        old = case.record_metadata()
        source = dict(case.response_source, captured_at='2026-09-08T12:00:00Z')
        case.put('source', source['id'], source)
        def check(records, artifacts):
            metadata.validate_citation_metadata(records, artifacts, metadata.find_citation_metadata(records, case.version))
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, case.payload('current-metadata'))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertEqual(case.store.snapshot()['records']['problem_citation_metadata'][old['id']], old)

    def test_citation_locator_step_replaces_a_changed_source_binding(self):
        from test_research_citation_locators import CitationLocatorTests
        from research_harness import problem_evidence
        case = self.prepare_fixture(CitationLocatorTests)
        old = case.mutate(cli.OPERATIONS['problem-citation-locator'], case.build_payload())['result']
        source = case.store.snapshot()['records']['source'][old['source_id']]
        case.put('source', source['id'], dict(source, captured_at='2026-09-08T12:00:00Z'))
        def check(records, artifacts):
            latest = max(records['citation_locator'].values(), key=lambda row: row['recorded_revision'])
            problem_evidence.validate_citation_locator(records, artifacts, latest)
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, dict(case.build_payload(), id='current-locator'))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertEqual(case.store.snapshot()['records']['citation_locator'][old['id']], old)

    def test_read_step_completes_the_current_original_without_changing_the_earlier_reading(self):
        from test_research_problem_value_readings import ProblemValueReadingTests
        from research_harness import problem_evidence
        case = self.prepare_fixture(ProblemValueReadingTests)
        second = case.capture_second_original(read=False)
        held = copy.deepcopy(case.store.snapshot()['records']['reading'])
        bundle = case.store.snapshot()['records']['source_bundle']['second-body']
        def check(records, artifacts):
            problem_evidence.validate_reading(records, artifacts, case.first)
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, case.full_note(bundle, second['reading_id']))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertTrue(all(case.store.snapshot()['records']['reading'][key] == row for key, row in held.items()))

    def test_round_search_and_exemplar_steps_clear_their_own_obligations(self):
        from rounds_fixtures import RoundsCase
        from research_harness import literature
        case = self.prepare_fixture(RoundsCase)
        case.open_round()
        def check_for(code):
            def check(records, artifacts):
                self.require_obligation(literature.foundation_state(records, artifacts, 'research')['obligations'], code)
            return check
        before = self.take_boundary_plan(case, check_for('round_search_missing'))
        step = before['details']['decision']['then'][0]['steps'][0]
        self.assertNotIn('unlisted', before['details'])
        self.assertEqual(step['command'], 'search')
        for purpose in literature.DEVELOPMENT_PURPOSES:
            with patch('test_research_synthesis.record_search', wraps=cli.OPERATIONS[step['command']]) as record:
                case.record_purpose(purpose, purpose + '-current')
            self.assertEqual(set(record.call_args[0][1]), set(step['chooses']))
        self.assertEqual(self.take_boundary_plan(case, check_for('round_search_missing'))['state'], 'done')
        before = self.take_boundary_plan(case, check_for('round_exemplar_missing'))
        self.execute_step(case, before, dict(id='current-exemplar', profile='research',
            version_id=case.links[1]['version_id'], reason='A comparable development exemplar.'))
        self.assertEqual(self.take_boundary_plan(case, check_for('round_exemplar_missing'))['state'], 'done')

    def test_carry_step_refreshes_the_source_cut_and_keeps_the_previous_carry(self):
        from test_problem_objection_carry import ProblemObjectionCarryTests, legacy_handoff_or_carry_adapter
        from research_harness import problem_objection_carry
        fixture = self.prepare_fixture(ProblemObjectionCarryTests)
        source = fixture.fixture()
        source.record_slate()
        case = fixture.fixture('math.PR', source=source)
        legacy_handoff_or_carry_adapter(case, source)
        held = copy.deepcopy(case.store.snapshot()['records']['problem_objection_carry'])
        source.assess('slate', 'first', objections=[dict(id='new-objection', claim='A second boundary matters.',
            reason='The original source records the boundary.', evidence=[source.supporting],
            resolution_condition='Assess the boundary.', candidate_ids=[])])
        def check(records, artifacts):
            problem_objection_carry.require_fresh_sources(records, artifacts)
        before = self.take_boundary_plan(case, check)
        self.execute_step(case, before, dict(id='current-carry', source_workspace=str(source.root),
                                            source_revision=source.store.revision))
        self.assertEqual(self.take_boundary_plan(case, check)['state'], 'done')
        self.assertTrue(all(case.store.snapshot()['records']['problem_objection_carry'][key] == row
                            for key, row in held.items()))

    def test_native_delivery_step_resolves_both_unbound_references(self):
        for code in ('problem_attempt_unbound', 'problem_result_unbound'):
            self.assertIn(code, stop_classes.STOP_REGISTRY)
        script = (ROOT / 'tests/fixtures/series_decision_delivery.py.txt').read_text()
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT / 'skills/math-solver/harness',
            env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
