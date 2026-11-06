"""A posted claim terminates only the resolved review head of that claim."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response
from problem_parts_fixtures import ResultPartsCase
from openness_reading_fixtures import OpennessReadingCase
from research_harness import cli, problem_claims, record_check, review_continuation
from research_harness import review_protocol as protocol, review_series
from research_harness.errors import ResearchError
from research_harness.output_views import render_all


class PostedHeadPlanningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ResultPartsCase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.assignment = self.fixture.assign('result-first')

    def post(self, case=None, identifier='first-post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116'):
        case = case or self.case
        statement = problem_claims.find_current_statement(case.store.snapshot()['records'])
        return case.mutate(problem_claims.record_post_external, dict(
            id=identifier, problem_id=problem_id,
            statement_digest=problem_claims.compute_statement_digest(statement),
            reason='The owner published this synthetic statement.',
            evidence='Synthetic server publication receipt.'))

    def plan(self, records, identifier=None):
        return review_continuation.plan_next(records, self.case.artifacts,
                                             identifier or self.assignment['id'])

    def read_post_bytes(self, identifier):
        with self.case.store._connection() as connection:
            row = connection.execute("SELECT value FROM records WHERE kind = 'problem_post' AND key = ?",
                                     (identifier,)).fetchone()
        return row['value'].encode()

    def test_second_statement_series_runs_records_and_posts_independently(self):
        first = self.post()
        render_all(self.case.store)
        first_bytes = self.read_post_bytes(first['result']['id'])
        statement = dict(self.case.statement, id='statement-2',
                         statement='Every synthetic graph in this family has a finite realization bound.')
        self.case.put('problem_statement', statement['id'], statement)
        self.case.put('problem_selection', 'current', dict(statement_id=statement['id']))
        second = self.case.assign('result', 'first', identifier='second-result')
        steps, calls = [], []

        def respond(route, request, credential):
            calls.append(request)
            return build_codex_response(route, request, self.case.build_review_output('result'))

        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            code = review_series.run_series(self.case.store, second['id'], emit=steps.append)
        self.assertEqual(code, 0, steps)
        self.assertEqual([step['state'] for step in steps], ['run', 'record', 'done'])
        self.assertEqual(len(calls), 1)
        records = self.case.read_records()
        attempt = next(row for row in records['review_attempt'].values()
                       if row['assignment_id'] == second['id'])
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, second, attempt))
        self.assertTrue(any(row['assignment_id'] == second['id'] for row in records['value_review'].values()))
        second_post = self.post(identifier='second-post', problem_id='656c336e-e892-4c47-80d2-a71d022f4117')['result']
        self.assertNotEqual(first['result']['statement_digest'], second_post['statement_digest'])
        self.assertNotEqual(first['result']['problem_id'], second_post['problem_id'])
        self.assertEqual(len(self.case.read_records()['problem_post']), 2)
        self.assertEqual(self.case.read_records()['problem_post'][first['result']['id']], first['result'])
        render_all(self.case.store)
        self.assertEqual(self.read_post_bytes(first['result']['id']), first_bytes)
        self.assertEqual(cli.status_report(self.case.store)['next']['code'], 'problem_already_posted')

    def test_posted_aliases_stop_but_changed_claim_or_criteria_plan_normally(self):
        self.post()
        for role in ('bar', 'result', 'adjudicator'):
            for field in (None, 'statement', 'resolution_criteria'):
                with self.subTest(role=role, changed=field):
                    records = self.case.read_records()
                    statement = dict(self.case.statement, id='alias', title='Another title')
                    if field is not None:
                        statement[field] = 'A different synthetic ' + field
                    records['problem_statement']['alias'] = statement
                    assignment = records['review_assignment'][self.assignment['id']]
                    assignment.update(role=role, subject=dict(kind='problem', statement_id='alias'))
                    assignment['prompt'] = protocol.canonical_prompt(role, orchestration='problem_creation')
                    assignment['prompt_hash'] = assignment['prompt']['rendered_prompt_hash']
                    unposted = copy.deepcopy(records)
                    unposted.pop('problem_post')
                    before = self.plan(unposted)
                    self.assertEqual(before['state'], 'run')
                    after = self.plan(records)
                    if field is None:
                        self.assertEqual(after['state'], 'done')
                        self.assertNotIn('command', after)
                        self.assertNotIn('payload', after)
                    else:
                        self.assertEqual(after, before)

    def test_excluded_roles_survive_and_old_unbound_slots_close(self):
        self.post()
        for role, subject in [
                (role, dict(kind='problem', statement_id='statement-1'))
                for role in ('verification', 'manuscript', 'standalone')
        ] + [('bar', subject) for subject in (
                {}, dict(kind='problem', statement_id=None),
                dict(kind='study', statement_id='statement-1'))]:
            with self.subTest(role=role, subject=subject):
                records = self.case.read_records()
                assignment = records['review_assignment'][self.assignment['id']]
                assignment.update(role=role, subject=subject)
                assignment['recorded_revision'] = self.case.store.revision + 1
                orchestration = 'problem_creation' if subject.get('kind') == 'problem' else None
                assignment['prompt'] = protocol.canonical_prompt(role, orchestration=orchestration)
                assignment['prompt_hash'] = assignment['prompt']['rendered_prompt_hash']
                unposted = copy.deepcopy(records)
                unposted.pop('problem_post')
                before = self.plan(unposted)
                self.assertEqual(before['state'], 'run')
                if role == 'bar' and subject.get('kind') in (None, 'problem'):
                    self.assertEqual(self.plan(records)['state'], 'done')
                else:
                    self.assertEqual(self.plan(records), before)

    def prepare_native_case(self):
        self.addCleanup(sys.modules.__setitem__, 'research_harness.problem_claims',
                        sys.modules['research_harness.problem_claims'])
        sys.modules['research_harness.problem_claims'] = problem_claims
        case = OpennessReadingCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def test_independent_native_math_keeps_pending_plan_after_post(self):
        case = self.prepare_native_case()
        reference = case.export(assignment_id='native', name='native-packet')
        packet = json.loads(case.artifacts.read(reference))
        packet['contract'].pop('orchestration')
        packet['scientific_context'].pop('openness_minimum')
        reference = case.artifacts.put(json.dumps(packet).encode(), 'application/json')
        case.assign('native', reference)
        records = case.store.snapshot()['records']
        before = review_continuation.plan_next(records, case.artifacts, 'native')
        self.assertEqual(before['state'], 'run')
        self.post(case)
        self.assertEqual(review_continuation.plan_next(case.store.snapshot()['records'], case.artifacts,
                                                      'native'), before)

    def test_native_openness_closes_pending_plan_after_post(self):
        case = self.prepare_native_case()
        case.assign('openness', case.export(assignment_id='openness'))
        self.post(case)
        self.assertEqual(review_continuation.plan_next(case.store.snapshot()['records'], case.artifacts,
                                                      'openness')['state'], 'done')

    def test_posted_successor_removes_ancestor_navigation_fields(self):
        self.post()
        records = self.case.read_records()
        ancestor = records['review_assignment'][self.assignment['id']]
        successor = dict(ancestor, id='posted-successor', context=dict(prior_assignment_id=ancestor['id']),
                         history_assignment_id=ancestor['id'], recorded_revision=560)
        ancestor.update(subject=dict(kind='problem', statement_id=None), recorded_revision=541)
        records['review_assignment'][successor['id']] = successor
        hint = dict(kind='review', workflow='review-assignment', scope=dict(prior_assignment_id=ancestor['id']),
                    command='review-run', payload=dict(assignment_id=ancestor['id']), request_id='stale',
                    label='Keep the finding label')
        original = copy.deepcopy(hint)
        plan = self.plan(records)
        self.assertEqual((plan['head_id'], plan['state']), ('posted-successor', 'done'))
        actions = review_continuation.describe_next_actions(records, self.case.artifacts, ancestor['id'], [hint])
        self.assertEqual(actions, [dict(kind='review', state='done', label='Keep the finding label')])
        self.assertEqual(hint, original)

    def test_cross_statement_slot_successor_still_resolves_to_revision_560(self):
        records = self.case.read_records()
        ancestor = records['review_assignment'][self.assignment['id']]
        ancestor.update(role='result', recorded_revision=541)
        newer = dict(self.case.statement, id='statement-4', statement='A different synthetic bound.')
        records['problem_statement']['statement-4'] = newer
        successor = dict(ancestor, id='statement-4-result', subject=dict(kind='problem', statement_id='statement-4'),
                         recorded_revision=560)
        records['review_assignment'][successor['id']] = successor
        records['problem_selection']['current']['statement_id'] = 'statement-4'
        before = self.plan(records)
        self.assertEqual((before['head_id'], before['state']), ('statement-4-result', 'run'))
        hints = [dict(kind='review', workflow='review-run', scope=dict(assignment_id=ancestor['id']))]
        before_actions = review_continuation.describe_next_actions(records, self.case.artifacts,
                                                                  ancestor['id'], hints)
        self.post()
        records['problem_post'] = self.case.read_records()['problem_post']
        self.assertEqual(review_continuation.list_chain_heads(records, self.case.artifacts),
                         ['bar-first', 'bar-second', 'statement-4-result'])
        self.assertEqual(self.plan(records), before)
        self.assertEqual(review_continuation.describe_next_actions(records, self.case.artifacts,
                                                                  ancestor['id'], hints), before_actions)

    def test_record_check_checks_excluded_head_and_hands_off_scientific_rejection(self):
        self.addCleanup(sys.modules.__setitem__, 'research_harness.problem_claims',
                        sys.modules['research_harness.problem_claims'])
        sys.modules['research_harness.problem_claims'] = problem_claims
        case = OpennessReadingCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        original_statement_id = case.selected['id']
        case.selected = case.select_statement(identifier='second-statement',
                                               statement='A distinct complete synthetic problem.')
        for index, search in enumerate(case.searches):
            search = dict(search, question=case.selected['statement'],
                          queries=search['queries'] + [case.selected['statement']])
            case.put('literature_search', search['id'], search)
            case.searches[index] = search
        for identifier in ('pending-openness', 'rejected-openness'):
            case.assign(identifier, case.export(assignment_id=identifier, name=identifier + '-packet'))
        packet = case.read_packet('rejected-openness')['native']
        case.invoke('rejected-openness', dict(schema_version=2, subject_digest=packet['subject_digest'],
            claim_digest=packet['claim_digest'], reviewer=packet['reviewer'], decision='reject',
            reason='The synthetic evidence does not establish this claim.', objections=[]))
        case.put('problem_selection', 'current', dict(statement_id=original_statement_id))
        # A matching pending bar remains terminal beside the excluded native heads.
        pending = case.mutate(protocol.record_assignment, dict(id='pending-bar', role='bar', route_id='route',
            dossier_id=None, reviewer_id='bar-reviewer', author_id='problem-author', context={}))['result']
        self.post(case)
        checked = []
        original = record_check.check_prospective_request

        def check_request(records, artifacts, plan):
            checked.append(plan['head_id'])
            return original(records, artifacts, plan)

        before = case.store.snapshot()
        with patch.object(record_check, 'check_prospective_request', side_effect=check_request), \
                patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Offline check')):
            report = record_check.check_continuation(case.store)
        chains = {chain['head_id']: chain for chain in report['chains']}
        self.assertEqual(chains[pending['id']]['boundary'], 'done')
        self.assertEqual(chains['pending-openness']['boundary'], 'run')
        self.assertEqual(chains['rejected-openness']['boundary'], 'handoff')
        self.assertEqual(chains['rejected-openness']['actions'][-1]['code'], 'openness_review_not_approved')
        self.assertEqual(checked, ['pending-openness'])
        self.assertTrue(chains['pending-openness']['request']['request_digest'])
        self.assertEqual(case.store.snapshot(), before)
        self.assertNotIn('problem_already_posted', record_check.EXPECTED_STOP_CODES)
        for code in ('review_input_too_large', 'unknown_fixture_stop'):
            with self.subTest(unqualified=code), patch.object(record_check, 'check_prospective_request',
                    side_effect=ResearchError(code, 'Synthetic unqualified request refusal')):
                with self.assertRaises(ResearchError) as caught:
                    record_check.check_continuation(case.store)
                self.assertEqual(caught.exception.code, code)
