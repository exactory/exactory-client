"""Harness-derived accounts retain checks, corrections and their evidence cut."""

import copy
import unittest

from research_harness import problem_accounts, problem_claims, problem_delivery, problem_evidence, problem_forming
from research_harness.evidence import digest
import test_problem_accounts_tests as credit_fixtures
from test_research_problem_forming import build_form, build_forming, build_outcome
from test_research_problem_objective_results import build_objective_native, build_objective_result
from test_research_problem_attempt import put_native_attempt


class DerivedAccountsTests(unittest.TestCase):
    def setUp(self):
        self.case = credit_fixtures.AccountTestCreditTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()

    def project(self, records=None):
        case = self.case
        if records is None:
            case.record()
            records = case.store.snapshot()['records']
        projected = problem_evidence.describe_evidence(records, case.artifacts)
        self.assertIn('derived_accounts', projected['value'])
        return projected

    def test_all_runs_on_the_text_keep_each_failure_status_and_their_plans(self):
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        original = records['execution'][case.observed['id']]
        # An unassessed repeat is still a check, even though the author never cites it.
        repeat = copy.deepcopy(original)
        repeat.update(id='uncited-run', recorded_revision=case.store.revision + 1)
        repeat['payload']['id'] = repeat['id']
        records['execution'][repeat['id']] = repeat
        assessment = records['cycle_assessment']['assessment-1']
        assessment['payload']['failures'][0]['status'] = 'observed'
        projected = self.project(records)
        rows = projected['value']['derived_accounts']['checks']['current']
        self.assertEqual([row['execution_id'] for row in rows], [original['id'], 'uncited-run'])
        self.assertEqual([row['failure_signals'][0]['status'] for row in rows], ['observed', 'unresolved'])
        self.assertEqual(rows[0]['statement_digest'], problem_claims.compute_statement_digest(case.statement))
        self.assertEqual(rows[0]['question'], case.planned['payload']['question'])
        self.assertEqual(rows[0]['plan_id'], 'cycle-1')
        self.assertEqual(rows[0]['statement_id'], case.statement['id'])
        self.assertEqual(rows[0]['resolution_criteria'], case.statement['resolution_criteria'])
        self.assertIsNone(rows[1]['assessment_id'])
        held = {(row['cite']['record_kind'], row['cite']['id']): row['record'] for row in projected['account_records']}
        self.assertEqual(held[('cycle_plan', 'cycle-1')], case.planned)

    def test_same_text_under_another_id_keeps_runs_and_earlier_text_keeps_failed_test(self):
        case = self.case
        case.record_correction(case.observed['id'])
        case.record()
        records = case.store.snapshot()['records']
        records['cycle_assessment']['assessment-1']['payload']['failures'][0]['status'] = 'observed'
        current = records['problem_statement']['correction-statement']
        same = dict(case.statement, id='another-id', recorded_revision=100)
        records['problem_statement'][same['id']] = same
        records['cycle_plan']['cycle-1']['payload']['objective']['id'] = same['id']
        derived = self.project(records)['value']['derived_accounts']
        self.assertEqual(derived['checks']['current'], [])
        self.assertEqual(derived['checks']['earlier'][0]['statement'], case.statement['statement'])
        self.assertEqual(derived['checks']['earlier'][0]['failure_signals'][0]['status'], 'observed')
        self.assertNotEqual(derived['checks']['earlier'][0]['statement_digest'], problem_claims.compute_statement_digest(current))

    def test_test_lists_all_computations_before_the_plan_with_questions(self):
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        before = case.planned['planned_revision'] - 1
        records['probe'] = {'old-probe': {'id': 'old-probe', 'question': 'An earlier quantity?', 'revision': before},
                            'late-probe': {'id': 'late-probe', 'question': 'After freezing?', 'revision': before + 2}}
        earlier = copy.deepcopy(records['execution'][case.observed['id']])
        earlier.update(id='old-run', recorded_revision=before)
        records['execution'][earlier['id']] = earlier
        rows = self.project(records)['value']['derived_accounts']['checks']['current']
        tested = next(row for row in rows if row['execution_id'] == case.observed['id'])
        self.assertEqual(tested['test_ids'], ['test'])
        self.assertEqual([(row['kind'], row['id'], row['question']) for row in tested['earlier_computations']],
                         [('execution', 'old-run', case.planned['payload']['question']), ('probe', 'old-probe', 'An earlier quantity?')])

    def test_history_follows_previous_and_delivers_calls_and_all_outcomes_with_counts(self):
        case = self.case
        case.mutate(problem_forming.record_form_outcome, build_outcome())
        case.record_correction(case.observed['id'])
        case.mutate(problem_forming.record_form_outcome, build_outcome('latest', outcome='Still unresolved.'))
        unrelated = build_forming('other-call', forms=[build_form('other-form')], comparison_count=7)
        case.mutate(problem_forming.record_forming, unrelated)
        projected = self.project()
        derived = projected['value']['derived_accounts']
        history = derived['history']
        self.assertEqual([row['form']['id'] for row in history], ['form-1', 'corrected'])
        self.assertEqual(history[1]['form']['previous']['reason'], 'An independent structural correction.')
        self.assertEqual(history[0]['outcomes'][0]['id'], 'latest')
        self.assertEqual(history[0]['forming_call_id'], 'forming-1')
        self.assertEqual(derived['counts']['lineage'], {'forming_calls': 2, 'forms': 2, 'recorded_comparisons': 0, 'probes': 0, 'runs': 1})
        self.assertEqual(derived['counts']['exploration'], {'forming_calls': 3, 'forms': 3, 'recorded_comparisons': 7, 'probes': 0, 'runs': 1})
        held = {(row['cite']['record_kind'], row['cite']['id']) for row in projected['account_records']}
        self.assertTrue({('forming_call', 'forming-1'), ('forming_call', 'correction'), ('form_outcome', 'outcome-1'), ('form_outcome', 'latest')} <= held)
        self.assertNotIn(('forming_call', 'other-call'), held)

    def test_later_run_makes_accounts_stale_but_a_plan_or_other_text_does_not(self):
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        before = problem_claims.compute_problem_evidence_digest(records, case.artifacts)
        later = copy.deepcopy(records['execution'][case.observed['id']])
        later.update(id='new-run', recorded_revision=case.store.revision + 1)
        records['execution'][later['id']] = later
        pending = problem_delivery.assess_minimum(records, case.artifacts)
        stale = [row for row in pending if row['code'] == 'problem_value_stale']
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['value_id'], 'value-1')
        self.assertEqual(stale[0]['changes'], [{'kind': 'execution', 'id': 'new-run', 'reason': 'later_run_on_current_text'}])
        self.assertNotEqual(problem_claims.compute_problem_evidence_digest(records, case.artifacts), before)
        records['problem_value']['value-1']['recorded_revision'] = later['recorded_revision'] + 1
        self.assertFalse(any(row['code'] == 'problem_value_stale' for row in problem_delivery.assess_minimum(records, case.artifacts)))
        records['problem_value']['value-1']['recorded_revision'] -= 2
        records['problem_statement']['other-text'] = dict(case.statement, id='other-text', statement='A different text.')
        records['cycle_plan']['cycle-1']['payload']['objective']['id'] = 'other-text'
        self.assertFalse(any(row['code'] == 'problem_value_stale' for row in problem_delivery.assess_minimum(records, case.artifacts)))

    def test_later_history_outcome_and_objective_snapshot_share_one_currency_obligation(self):
        case = self.case
        native = build_objective_native()
        reference = build_objective_result(put_native_attempt(case, native))
        case.value['accounts']['explanatory_use'][0]['evidence'].append(reference)
        case.record()
        case.mutate(problem_forming.record_form_outcome, build_outcome())
        native['totals']['moves'] -= 1
        latest = put_native_attempt(case, native)
        records = case.store.snapshot()['records']
        stale = [row for row in problem_delivery.assess_minimum(records, case.artifacts) if row['code'] == 'problem_value_stale']
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['changes'], [
            {'kind': 'form_outcome', 'id': 'outcome-1', 'reason': 'later_outcome_in_history'},
            {'kind': 'native_attempt', 'id': latest, 'reason': 'later_snapshot_of_cited_objective',
             'previous_native_attempt_digest': reference['native_attempt_digest']}])
        self.assertFalse(any(row['code'] == 'problem_result_superseded' for row in problem_delivery.assess_minimum(records, case.artifacts)))

    def test_missing_test_assessment_is_a_stable_refusal(self):
        case = self.case
        records = case.store.snapshot()['records']
        records['cycle_assessment'] = {}
        accounts = case.value['accounts']
        entries = {row['id']: row for row in problem_accounts.collect_entries(accounts)}
        case.assert_error('problem_test_run_invalid', lambda: problem_accounts.validate_test(records,
            accounts['checks'][0], entries, {'weaker': accounts['alternatives'][0]}, case.statement, None))

    def test_tier_one_delivers_uncited_checks_without_accepting_author_derived_fields(self):
        case = self.case
        case.value['accounts'].pop('checks')
        case.value['accounts'].pop('gain')
        case.value['accounts'].pop('search_coverage')
        case.value['accounts']['answer_evidence'] = []
        projected = self.project()
        row = projected['value']['derived_accounts']['checks']['current'][0]
        self.assertEqual(row['test_ids'], [])
        self.assertNotIn('earlier_computations', row)
        self.assertEqual(row['failure_signals'][0]['status'], 'not_observed')
        for location in ('value', 'accounts'):
            value = copy.deepcopy(case.value)
            value['id'] = 'author-derived-' + location
            (value if location == 'value' else value['accounts'])['derived_accounts'] = {}
            case.assert_error('invalid_input', lambda: case.mutate(problem_claims.record_value, value))

    def test_each_failure_signal_uses_the_latest_run_assessment_or_unresolved(self):
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        plan = records['cycle_plan']['cycle-1']['payload']
        plan['failure_signals'].extend([dict(plan['failure_signals'][0], id='second'),
                                       dict(plan['failure_signals'][0], id='unassessed')])
        latest = copy.deepcopy(records['cycle_assessment']['assessment-1'])
        latest['payload'].update(id='assessment-latest', failures=[
            dict(latest['payload']['failures'][0], status='observed'),
            dict(latest['payload']['failures'][0], signal_id='second', status='not_observed')])
        latest['assessed_revision'] += 1
        records['cycle_assessment']['assessment-latest'] = latest
        row = self.project(records)['value']['derived_accounts']['checks']['current'][0]
        self.assertEqual(row['assessment_id'], 'assessment-latest')
        self.assertEqual([signal['status'] for signal in row['failure_signals']], ['observed', 'not_observed', 'unresolved'])

    def test_a_plan_alone_and_an_unrelated_outcome_do_not_stale_accounts(self):
        case = self.case
        case.record()
        case.mutate(problem_forming.record_forming, build_forming('unrelated', forms=[build_form('unrelated-form')]))
        case.mutate(problem_forming.record_form_outcome, build_outcome('unrelated-outcome', form_id='unrelated-form'))
        records = case.store.snapshot()['records']
        plan = copy.deepcopy(case.planned)
        plan['payload']['id'] = 'unexecuted-plan'
        plan['planned_revision'] = case.store.revision + 1
        records['cycle_plan']['unexecuted-plan'] = plan
        self.assertFalse(any(row['code'] == 'problem_value_stale' for row in problem_delivery.assess_minimum(records, case.artifacts)))

    def test_status_and_post_gate_name_the_same_changed_record(self):
        from research_harness import cli
        from research_harness.gates import gate_state
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        later = copy.deepcopy(records['execution'][case.observed['id']])
        later.update(id='later-for-status', recorded_revision=case.store.revision + 1)
        later['payload']['id'] = later['id']
        case.put('execution', later['id'], later)
        records = case.store.snapshot()['records']
        report = cli.status_report(case.store)
        gate = gate_state(records, case.artifacts, 'post')
        report_stale = [row for row in report['obligations'] if row['code'] == 'problem_value_stale']
        self.assertEqual(report_stale, [row for row in gate['obligations'] if row['code'] == 'problem_value_stale'])
        self.assertEqual(report_stale[0]['changes'][0]['id'], later['id'])
        self.assertFalse(gate['ready'])

    def test_cited_legacy_attempt_results_also_track_later_objective_snapshots(self):
        from research_harness import problem_attempt
        from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt
        case = self.case
        native = build_native_attempt(case.statement['statement'], [build_acceptance()], {})
        initial = put_native_attempt(case, native)
        case.mutate(problem_attempt.record_attempt, build_attempt(native_attempt_digest=initial, statement_id=case.statement['id']))
        reference = {'kind': 'result', 'attempt_id': 'attempt-1', 'acceptance_id': 'acceptance-1'}
        case.value['accounts']['explanatory_use'][0]['evidence'].append(reference)
        case.record()
        native['totals']['moves'] -= 1
        latest = put_native_attempt(case, native)
        records = case.store.snapshot()['records']
        stale = [row for row in problem_delivery.assess_minimum(records, case.artifacts) if row['code'] == 'problem_value_stale']
        self.assertEqual(stale[0]['changes'], [{'kind': 'native_attempt', 'id': latest,
            'reason': 'later_snapshot_of_cited_objective', 'previous_native_attempt_digest': initial}])

    def test_lineage_counts_keep_computations_from_superseded_outcomes_and_forming_evidence(self):
        case = self.case
        case.record()
        base = case.store.snapshot()['records']
        for provenance in ('superseded-outcome', 'input', 'place', 'ground', 'later-call'):
            with self.subTest(provenance=provenance):
                records = copy.deepcopy(base)
                probe = {'id': 'linked-probe', 'revision': 1, 'question': 'A quantity used by the form.'}
                records['probe'] = {probe['id']: probe, 'unrelated': dict(probe, id='unrelated')}
                reference = {'kind': 'record', 'record_kind': 'probe', 'id': probe['id'], 'digest': digest(probe)}
                call = records['forming_call']['forming-1']
                if provenance == 'superseded-outcome':
                    old = dict(build_outcome(), recorded_revision=1, evidence=[{
                        'reference': {'kind': 'probe', 'id': probe['id']}, 'standing': 'exploratory'}])
                    records['form_outcome'] = {old['id']: old, 'latest': dict(build_outcome('latest'), recorded_revision=2)}
                elif provenance == 'input':
                    call['inputs'] = [{'description': 'Used values.', 'evidence': [reference]}]
                elif provenance == 'place':
                    call['question']['places'] = [{'description': 'A place.', 'evidence': [reference]}]
                elif provenance == 'ground':
                    call['forms'][0]['grounds'][0]['evidence'] = [reference]
                else:
                    records['forming_call']['later-call'] = dict(build_forming('later-call', forms=[], probes=[
                        {'probe_id': probe['id'], 'form_id': 'form-1', 'role': 'observation'}]), recorded_revision=100)
                records['problem_candidate']['candidate-history']['source']['evidence'][0]['digest'] = digest(call)
                projected = self.project(records)
                counts = projected['value']['derived_accounts']['counts']
                if provenance == 'later-call':
                    self.assertIn(('forming_call', 'later-call'),
                        {(row['cite']['record_kind'], row['cite']['id']) for row in projected['account_records']})
                self.assertEqual(counts['lineage']['probes'], 1)
                self.assertEqual(counts['exploration']['probes'], 2)
                self.assertEqual(counts['lineage']['forming_calls'], 2 if provenance == 'later-call' else 1)

    def test_lineage_run_counts_include_outcomes_and_grounds_on_other_texts(self):
        case = self.case
        case.record()
        base = case.store.snapshot()['records']
        for provenance in ('outcome', 'ground'):
            with self.subTest(provenance=provenance):
                records = copy.deepcopy(base)
                records['problem_statement']['other'] = dict(case.statement, id='other', statement='Another proposition.')
                plan = copy.deepcopy(records['cycle_plan']['cycle-1'])
                plan['payload'].update(id='other-cycle', objective={'id': 'other', 'statement': 'Another proposition.'})
                records['cycle_plan']['other-cycle'] = plan
                execution = copy.deepcopy(records['execution'][case.observed['id']])
                execution.update(id='history-run', recorded_revision=1)
                execution['payload'].update(id='history-run', cycle_id='other-cycle')
                records['execution']['history-run'] = execution
                call = records['forming_call']['forming-1']
                if provenance == 'outcome':
                    records['form_outcome'] = {'history-outcome': dict(build_outcome('history-outcome'), recorded_revision=1,
                        evidence=[{'reference': {'kind': 'computation', 'execution_id': 'history-run', 'output_id': 'result', 'formal_check': None}, 'standing': 'admissible'}])}
                elif provenance == 'ground':
                    call['forms'][0]['grounds'][0]['evidence'] = [{'kind': 'record', 'record_kind': 'execution',
                        'id': 'history-run', 'digest': digest(execution)}]
                records['problem_candidate']['candidate-history']['source']['evidence'][0]['digest'] = digest(call)
                derived = self.project(records)['value']['derived_accounts']
                self.assertEqual(len(derived['checks']['current']), 1)
                self.assertEqual(derived['counts']['lineage']['runs'], 2)

    def test_redelivery_of_an_older_snapshot_changes_the_cited_objective(self):
        case = self.case
        older = build_objective_native()
        older['totals']['moves'] -= 1
        older_id = put_native_attempt(case, older)
        current_id = put_native_attempt(case, build_objective_native())
        case.value['accounts']['explanatory_use'][0]['evidence'].append(build_objective_result(current_id))
        case.record()
        put_native_attempt(case, older)
        records = case.store.snapshot()['records']
        stale = [row for row in problem_delivery.assess_minimum(records, case.artifacts) if row['code'] == 'problem_value_stale']
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['changes'], [{'kind': 'native_attempt', 'id': older_id,
            'reason': 'later_snapshot_of_cited_objective', 'previous_native_attempt_digest': current_id}])

    def test_a_historical_value_without_accounts_has_no_new_currency_or_projection(self):
        from research_harness.problem_account_derivations import assess_account_currency
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        value = records['problem_value']['value-1']
        reference = value.pop('accounts')['explanatory_use'][0]['evidence'][0]
        for section in ('motivation', 'current_state'):
            value[section][0]['evidence'] = [reference]
        problem_evidence.validate_value(records, case.artifacts, value)
        before = (problem_claims.compute_problem_evidence_digest(records, case.artifacts),
                  problem_evidence.describe_evidence(records, case.artifacts),
                  problem_delivery.assess_post(records, case.artifacts))
        later = copy.deepcopy(records['execution'][case.observed['id']])
        later.update(id='later-uncited', recorded_revision=case.store.revision + 1)
        records['execution']['later-uncited'] = later
        records['form_outcome'] = {'later-outcome': dict(build_outcome('later-outcome'), recorded_revision=case.store.revision + 1)}
        self.assertEqual(assess_account_currency(records, value), [])
        self.assertEqual((problem_claims.compute_problem_evidence_digest(records, case.artifacts),
                          problem_evidence.describe_evidence(records, case.artifacts),
                          problem_delivery.assess_post(records, case.artifacts)), before)

    def test_correction_checks_count_their_runs_even_on_another_text(self):
        case = self.case
        case.record_correction(case.observed['id'])
        case.record()
        records = case.store.snapshot()['records']
        records['problem_statement']['other'] = dict(case.statement, id='other', statement='Another proposition.')
        plan = copy.deepcopy(records['cycle_plan']['cycle-1'])
        plan['payload'].update(id='other-cycle', objective={'id': 'other', 'statement': 'Another proposition.'})
        records['cycle_plan']['other-cycle'] = plan
        execution = copy.deepcopy(records['execution'][case.observed['id']])
        execution.update(id='correction-run', recorded_revision=1)
        execution['payload'].update(id='correction-run', cycle_id='other-cycle')
        records['execution']['correction-run'] = execution
        call = records['forming_call']['correction']
        call['forms'][0]['correction_checks'][0]['execution_id'] = 'correction-run'
        records['problem_candidate']['correction-candidate']['source']['evidence'][1]['digest'] = digest(call)
        derived = self.project(records)['value']['derived_accounts']
        self.assertEqual(derived['counts']['lineage']['runs'], 2)
        self.assertEqual(len(derived['checks']['earlier']), 1)

    def test_current_digest_keeps_runs_from_a_statement_outside_the_revision_chain(self):
        case = self.case
        case.record()
        records = case.store.snapshot()['records']
        # prepare_problem recorded statement-1 with the same text before this
        # candidate's statement. There is no revises_statement_id link to it.
        records['cycle_plan']['cycle-1']['payload']['objective']['id'] = 'statement-1'
        derived = self.project(records)['value']['derived_accounts']
        self.assertEqual([row['execution_id'] for row in derived['checks']['current']], [case.observed['id']])
        self.assertEqual(derived['checks']['earlier'], [])
