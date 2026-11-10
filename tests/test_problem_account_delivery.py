"""Current derived accounts and records must reach every form of result review."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

import problem_parts_fixtures as legacy_fixtures
import review_parts_fixtures as parts_fixtures
import test_review_staged_frontier as frontier
from research_harness import direction_reviews, research_decisions, review_staged
from research_harness import review_staged_protocol as producer, review_worker
from research_harness.evidence import digest


def install_accounts_delivery(case):
    # The scientific fixture isolates transport from account validation; real
    # account derivation and validation are exercised in test_problem_account_derivations.
    case.expected['value']['accounts'] = {'answer': {'id': 'answer', 'status': 'none'}}
    case.expected['value']['derived_accounts'] = {'checks': {'current': [], 'earlier': []}, 'history': [],
        'counts': {'lineage': {'forms': 0}, 'exploration': {'forms': 1}}}
    record = {'id': 'history-call', 'question': 'How was the earlier form corrected?'}
    case.put('forming_call', record['id'], record)
    case.expected['account_records'] = [{'record': record, 'cite': {
        'kind': 'record', 'record_kind': 'forming_call', 'id': record['id'], 'digest': digest(record)}}]
    if not case.expected['openness_searches']:
        search = {'id': 'search-accounts', 'queries': ['the earlier form']}
        case.put('literature_search', search['id'], search)
        case.expected['openness_searches'] = [{'search': search, 'sources': []}]


def assert_current_accounts(test, review):
    test.assertEqual(test.coverage(review), [])
    for field in ('accounts', 'derived_accounts'):
        expected = copy.deepcopy(test.case.expected)
        expected['value'][field] = {'changed': True}
        pending = test.coverage(review, expected)
        test.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])
    expected = copy.deepcopy(test.case.expected)
    expected['account_records'][0]['record']['question'] += ' Changed.'
    pending = test.coverage(review, expected)
    test.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])
    test.assertIn('account_records', pending[0]['missing'])


class OrdinaryAccountDeliveryTests(legacy_fixtures.ResultPartsCase):
    def test_single_input_requires_current_accounts_and_their_exact_records(self):
        install_accounts_delivery(self.case)
        assignment = self.assign()
        self.assertNotIn('problem_evidence_parts', self.packet(assignment))
        review = self.record_final(assignment)
        assert_current_accounts(self, review)


class LegacyPartsAccountDeliveryTests(legacy_fixtures.ResultPartsCase):
    def test_parts_repeat_current_value_and_require_account_records(self):
        self.oversized()
        install_accounts_delivery(self.case)
        turns, final = self.finish_parts()
        self.assertTrue(turns)
        for assignment in turns + [final]:
            self.assertEqual(self.packet(assignment)['problem_evidence']['value'], self.case.expected['value'])
        review = self.record_final(final)
        assert_current_accounts(self, review)

    def test_an_earlier_series_cannot_supply_current_account_records(self):
        self.oversized()
        install_accounts_delivery(self.case)
        _, old_final = self.finish_parts(first=self.assign('earlier'))
        self.record_final(old_final)
        self.case.expected['account_records'][0]['record']['question'] += ' Current correction.'
        self.case.expected['value']['derived_accounts']['counts']['exploration']['forms'] = 2
        assignment = self.assign('current', old_final['id'])
        index = 0
        while self.packet(assignment)['problem_evidence_parts']['stage'] == 'part':
            index += 1
            self.assertLess(index, 30)
            self.invoke(assignment)
            assignment = self.assign('current-turn-' + str(index), assignment['id'])
        final = assignment
        review = self.record_final(final)
        assert_current_accounts(self, review)


class PartsAccountDeliveryTests(parts_fixtures.PartsResultCase):
    def test_parts_repeat_current_value_and_require_account_records(self):
        self.oversized()
        install_accounts_delivery(self.case)
        turns, final = self.finish_series()
        self.assertTrue(turns)
        for assignment in turns + [final]:
            self.assertEqual(self.packet(assignment)['problem_evidence']['value'], self.case.expected['value'])
        review = self.record_final(final)
        assert_current_accounts(self, review)


class StagedAccountDeliveryTests(legacy_fixtures.ResultPartsCase):
    staged_entry = True
    output_tokens = 128000
    timeout_seconds = 60

    def test_staged_review_covers_the_current_account_frame_and_records(self):
        install_accounts_delivery(self.case)
        frame = {'problem_evidence': {key: self.case.expected[key] for key in ('value', 'account_records')}}
        manifest = review_staged.build_manifest(frame, [dict(key='account_records', index=0,
            item=self.case.expected['account_records'][0], artifacts=[], source_ids=[])], [])
        prior = self.assign('prior')
        with patch.object(producer, 'build_manifest', return_value=manifest):
            controller = self.case.assign('result', 'first', identifier='controller', staged_upgrade={'assignment_id': prior['id']})
        output = self.final_output()
        from research_harness.review_refusal import evidence_arrays
        for references in evidence_arrays(output):
            references[:] = [copy.deepcopy(self.case.expected['account_records'][0]['cite'])]
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        next_work = controller['next'][0]
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            for _ in range(30):
                result = frontier.FrontierTests.command(self, next_work['command'], next_work['payload'])
                if next_work['command'] == 'value-review':
                    review = result
                    break
                self.assertNotIn('refusal', result, result)
                next_work = result['next'][0]
            else:
                self.fail('The accounts series did not reach a result review')
        assert_current_accounts(self, review)
        records = self.case.read_records()
        graph = direction_reviews.collect_selected_task_graph(records, self.case.artifacts, controller['id'])
        omitted = copy.deepcopy(graph['selection'])
        source_ids = {row['source_id'] for row in graph['epoch']['manifest']['frame_fields']
                      if 'account_records' in row['path']}
        self.assertTrue(source_ids)
        for observed in omitted['observations']:
            observed['actual_input']['intervals'] = [span for span in observed['actual_input']['intervals']
                                                   if span['source_id'] not in source_ids]
        completion = review_staged.verify_completion(graph['epoch'], omitted, tasks=graph['tasks'])
        self.assertFalse(completion['complete'])
        with patch.object(direction_reviews, 'collect_selected_task_graph', return_value=dict(graph, completion=completion)):
            self.assertEqual(self.coverage(review)[0]['code'], 'problem_evidence_unassessed')


class AccountValueRefreshTests(unittest.TestCase):
    def test_recording_a_new_value_requires_and_accepts_refreshed_result_reviews(self):
        import test_research_problem_delivery as delivery_fixtures
        from research_harness import problem_claims
        case = delivery_fixtures.ProblemDeliveryCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_ready()
        records = case.store.snapshot()['records']
        before = problem_claims.compute_problem_evidence_digest(records, case.artifacts)
        value = copy.deepcopy(records['problem_value']['value-1'])
        value.pop('recorded_revision')
        value['id'] = 'value-refreshed'
        case.mutate(problem_claims.record_value, value)
        records = case.store.snapshot()['records']
        self.assertNotEqual(problem_claims.compute_problem_evidence_digest(records, case.artifacts), before)
        pending = research_decisions.assess_problem_reviews(records, case.artifacts)
        self.assertEqual(len([row for row in pending['obligations'] if row['code'] == 'problem_evidence_unassessed']), 2)
        for reviewer in ('first', 'second'):
            case.observe('result', reviewer, identifier='refreshed-' + reviewer)
        pending = research_decisions.assess_problem_reviews(case.store.snapshot()['records'], case.artifacts)
        self.assertTrue(pending['ready'], pending['obligations'])
