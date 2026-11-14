"""Ruling 22 verification reads every declared start and its stored attempts."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ci import route_capacity_profile as profile
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from test_review_route_capacity import build_route


class VerificationCriterionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = dict(review_route={'route': build_route()}, review_assignment={},
                            review_attempt={}, value_review={})
        self.declaration = dict(clone='synthetic-clone', route_id='route', max_calls=60,
                                recorded_revision=1)

    def put(self, value):
        return self.artifacts.put(json.dumps(value).encode(), 'application/json')

    def add_chain(self, count, over=(), *, identifier='chain', recorded=True):
        plan = dict(policy='exactory-route-capacity-policy-v1', profile=dict(
            request=dict(state='fits', share_at_or_over_limit=0.02)),
            task=dict(request=dict(material_allowance=120000, state='fits')))
        plan_ref = dict(protocol='exactory-route-capacity-plan-v1', digest=digest(plan),
                        artifact=self.put(plan))
        previous = None
        for number in range(count):
            key = identifier + '-' + str(number)
            assignment = dict(id=key, role='bar', route_id='route', reviewer_id=identifier,
                author_id='author', dossier_id=None, context=dict(prior_assignment_id=previous) if previous else {},
                recorded_revision=2 + 4 * number, capacity_plan=plan_ref,
                packet=self.put(dict(source_inventory=dict(protocol='exactory-independent-review-sources-v4'))))
            self.records['review_assignment'][key] = assignment
            attempt = dict(id=key, assignment_id=key, status='completed', origin='instrumented',
                output=self.put({}), response=None, task=dict(kind='request', characters=160000, material_allowance=120000),
                usage=dict(output_tokens=127999), completed_generation=True, recorded_revision=3 + 4 * number)
            if number in over:
                attempt.update(status='incomplete', output=None, completed_generation=False,
                               usage=dict(output_tokens=128000))
                replacement_id = key + '-replacement'
                replacement = dict(assignment, id=replacement_id, recorded_revision=4 + 4 * number)
                self.records['review_assignment'][replacement_id] = replacement
                retry = dict(attempt, id=key + '-retry', status='completed', output=self.put({}),
                    assignment_id=replacement_id, task=dict(kind='request', characters=100000, material_allowance=60000),
                    usage=dict(output_tokens=1200), completed_generation=True, recorded_revision=5 + 4 * number)
                self.records['review_attempt'][retry['id']] = retry
            self.records['review_attempt'][key] = attempt
            previous = key + '-replacement' if number in over else key
        if recorded:
            self.records['value_review'][identifier] = dict(id=identifier, assignment_id=previous)

    def assess(self):
        return profile.assess_verification_chain(self.records, self.artifacts, self.declaration)

    def test_candidate_b_boundaries_and_position_limit(self):
        for calls, over, expected in ((20, 1, 'pass'), (20, 2, 'fail'), (21, 2, 'pass'),
                                      (40, 2, 'pass'), (41, 3, 'pass'), (40, 3, 'fail')):
            with self.subTest(calls=calls, over=over):
                self.records['review_assignment'].clear()
                self.records['review_attempt'].clear()
                self.records['value_review'].clear()
                self.add_chain(calls - over, range(over))
                result = self.assess()
                self.assertEqual(result['result'], expected)
                self.assertEqual(result['calls'], calls)
                self.assertEqual(result['at_or_over_limit'], over)
                self.assertEqual(len(result['chains'][0]['attempts']), calls)

    def test_minimum_and_incomplete_started_chain_are_not_omitted(self):
        self.add_chain(12)
        self.assertEqual(self.assess()['result'], 'incomplete')
        self.add_chain(9, identifier='second')
        self.assertEqual(self.assess()['result'], 'pass')
        self.add_chain(2, identifier='unfinished', recorded=False)
        result = self.assess()
        self.assertEqual(result['result'], 'incomplete')
        self.assertEqual((result['calls'], len(result['chains'])), (23, 3))

    def test_combined_bound_rejects_five_individually_passing_chains(self):
        for number in range(5):
            self.add_chain(3, (0,), identifier='chain-' + str(number))
        result = self.assess()
        self.assertEqual((result['calls'], result['bound'], result['result']), (20, 1, 'fail'))
        self.assertEqual([chain['result'] for chain in result['chains']], ['pass'] * 5)

    def test_recorded_size_stop_owner_step_rules_and_plan_refuse_verification(self):
        self.add_chain(20)
        saved = copy.deepcopy(self.records)
        for change in ('size_stop', 'owner', 'old_rules', 'no_plan', 'late_declaration', 'call_budget'):
            with self.subTest(change=change):
                self.records = copy.deepcopy(saved)
                self.declaration.update(recorded_revision=1, max_calls=60)
                if change == 'size_stop':
                    self.records['review_series_stop'] = {'stop': dict(assignment_id='chain-0', code='review_output_over_cap')}
                elif change == 'owner':
                    self.records['review_context_event'] = {'owner': dict(assignment_id='chain-0', source='owner')}
                elif change == 'old_rules':
                    self.records['review_assignment']['chain-0']['packet'] = self.put({})
                elif change == 'no_plan':
                    self.records['review_assignment']['chain-0'].pop('capacity_plan')
                elif change == 'late_declaration':
                    self.declaration['recorded_revision'] = 4
                else:
                    self.declaration['max_calls'] = 19
                self.assertEqual(self.assess()['result'], 'fail')

    def test_two_over_limit_attempts_in_one_position_and_recorded_limit_answer_fail(self):
        self.add_chain(58, (0,))
        row = self.records['review_attempt']['chain-0']
        self.records['review_attempt']['again'] = dict(row, id='again', recorded_revision=4)
        self.assertEqual(self.assess()['result'], 'fail')
        self.records['review_attempt'].pop('again')
        self.records['review_attempt']['chain-0-retry']['usage']['output_tokens'] = 128000
        self.assertEqual(self.assess()['result'], 'fail')

    def test_pre_generation_failures_do_not_enlarge_bound_and_replacement_keeps_effort(self):
        self.add_chain(17, (0, 1))
        for number in range(5):
            row = dict(self.records['review_attempt']['chain-2'], id='failed-' + str(number),
                       status='failed', completed_generation=False, output=None, usage={},
                       error=dict(code='review_runtime_missing'))
            self.records['review_attempt'][row['id']] = row
        result = self.assess()
        self.assertEqual((result['calls'], result['result']), (19, 'fail'))
        self.assertEqual(len(result['chains'][0]['attempts']), 24)
        self.assertEqual([row['outcome'] for row in result['chains'][0]['attempts']
                          if row['id'].startswith('failed-')], ['runtime_unavailable'] * 5)

    def test_report_pins_policy_and_independent_upper_bound_without_mutating_records(self):
        self.add_chain(20)
        before = copy.deepcopy(self.records)
        result = self.assess()
        self.assertEqual(result['policy'], 'exactory-route-capacity-policy-v1')
        self.assertAlmostEqual(result['upper_share_95'], 0.1391083407, places=8)
        self.assertTrue(result['assumes_independent_attempts'])
        self.assertEqual(result['chains'][0]['initial_profile']['request']['state'], 'fits')
        self.assertEqual(self.records, before)

    def test_release_ceiling_is_shared_by_class_and_kind(self):
        samples = [dict(route_class=dict(adapter='claude_cli_v1', model='synthetic', effort='max'),
                        task=dict(kind='request'), usage=dict(output_tokens=128000 if i == 0 else 100),
                        completed_generation=True, output_limit=128000) for i in range(20)]
        self.assertEqual(profile.assess_release_output_share(samples)[0]['result'], 'pass')
        samples[1]['usage']['output_tokens'] = 128000
        self.assertEqual(profile.assess_release_output_share(samples)[0]['result'], 'fail')
        self.assertEqual(profile.assess_release_output_share(samples[:19])[0]['result'], 'incomplete')

    def test_signal_without_token_usage_counts_but_fallback_never_does(self):
        self.add_chain(19, (0,))
        attempt = self.records['review_attempt']['chain-0']
        attempt['usage'] = {}
        attempt['response'] = self.put(dict(id='provider', status='completed', output=[dict(type='message',
            content=[dict(type='output_text', text="API Error: Claude's response exceeded the 128000 output token maximum.")])]))
        self.assertEqual((self.assess()['calls'], self.assess()['at_or_over_limit']), (20, 1))
        response = json.loads(self.artifacts.read(attempt['response']))
        response['output'][0]['content'][0]['text'] = "API Error: Claude's response exceeded the 64000 output token maximum for a request sent without streaming."
        attempt.update(response=self.put(response), usage=dict(output_tokens=179808))
        self.assertEqual((self.assess()['calls'], self.assess()['at_or_over_limit']), (19, 0))

    def test_missing_replacement_and_changed_later_rules_fail(self):
        self.add_chain(19, (0,))
        self.records['review_attempt'].pop('chain-0-retry')
        self.assertIn('replacement_missing_or_not_smaller_at_same_effort', self.assess()['chains'][0]['reasons'])
        self.records['review_assignment']['chain-2']['packet'] = self.put({})
        self.assertIn('session_rules_are_not_v4', self.assess()['chains'][0]['reasons'])

    def test_real_shape_three_historical_over_cap_turns_fail_and_list_tokens(self):
        # Source: 20261112043216-bar-turn-over-output-cap-at-effort-max.
        self.add_chain(20, (0, 4, 7))
        for position, tokens in zip((0, 4, 7), (138371, 179808, 194065)):
            self.records['review_attempt']['chain-' + str(position)]['usage']['output_tokens'] = tokens
        result = self.assess()
        self.assertEqual(result['result'], 'fail')
        rows = [row for row in result['chains'][0]['attempts'] if row['at_or_over_limit']]
        self.assertEqual([row['tokens'] for row in rows], [138371, 179808, 194065])
        self.assertEqual(len(result['chains'][0]['positions']), 3)

    def test_policy_numbers_are_the_ruling_22_contract(self):
        from research_harness import review_route_capacity as capacity
        self.assertEqual((capacity.VERIFICATION_CALLS_PER_OVER_LIMIT,
            capacity.VERIFICATION_POSITION_OVER_LIMIT, capacity.VERIFICATION_MINIMUM_CALLS,
            capacity.VERIFICATION_RECORDED_OUTPUT_LIMIT), (20, 1, 20, 128000))

    def test_identical_retry_is_accepted_only_at_the_material_floor(self):
        self.add_chain(19, (0,))
        self.records['review_attempt']['chain-0-retry']['assignment_id'] = 'chain-0'
        self.records['review_attempt']['chain-0-retry']['task']['characters'] = 120000
        self.records['review_attempt']['chain-0-retry']['task']['material_allowance'] = 120000
        self.assertEqual(self.assess()['result'], 'fail')
        for key in ('chain-0', 'chain-0-retry'):
            self.records['review_attempt'][key]['task']['material_allowance'] = 15000
        self.assertEqual(self.assess()['result'], 'pass')

    def test_release_ceiling_uses_only_attempts_since_the_previous_release(self):
        sample = dict(route_class=dict(adapter='claude_cli_v1', model='synthetic', effort='max'),
            task=dict(kind='request'), usage=dict(output_tokens=128000), completed_generation=True, output_limit=128000)
        samples = [dict(sample, observed_at='2026-11-12T00:00:00Z') for _ in range(20)]
        samples.append(dict(sample, observed_at='2026-11-13T00:00:00Z'))
        rows = profile.assess_release_output_share(samples, since='2026-11-13T00:00:00Z')
        self.assertEqual((rows[0]['calls'], rows[0]['result']), (1, 'incomplete'))

    def test_completed_transport_without_single_generation_does_not_supply_a_call(self):
        self.add_chain(20)
        self.records['review_attempt']['chain-2']['completed_generation'] = False
        result = self.assess()
        self.assertEqual((result['calls'], result['result']), (19, 'incomplete'))

    def test_material_level_comes_from_frozen_plan_not_total_request_characters(self):
        self.add_chain(19, (0,))
        first = self.records['review_assignment']['chain-0']
        plan = json.loads(self.artifacts.read(first['capacity_plan']['artifact']))
        plan['task']['request'] = dict(material_allowance=120000, state='fits')
        reference = dict(first['capacity_plan'], digest=digest(plan), artifact=self.put(plan))
        for assignment in self.records['review_assignment'].values():
            assignment['capacity_plan'] = reference
        for attempt in self.records['review_attempt'].values():
            attempt['task'].pop('material_allowance', None)
            material = 59000 if attempt['id'] == 'chain-0-retry' else 119000
            attempt['task'].update(characters=material + 40000, material_characters=material, frame_characters=40000)
        self.assertEqual(self.assess()['result'], 'pass')

    def test_initial_profile_names_every_kind_with_its_state_and_share(self):
        self.add_chain(20)
        self.records['review_attempt']['chain-19']['task']['kind'] = 'final'
        self.assertIn('initial_profile_missing_kind', self.assess()['chains'][0]['reasons'])

    def test_a_second_max_route_start_cannot_be_left_out_of_the_declaration(self):
        self.add_chain(12)
        self.add_chain(9, identifier='other')
        route = dict(self.records['review_route']['route'], id='other-route')
        self.records['review_route'][route['id']] = route
        for assignment in self.records['review_assignment'].values():
            if assignment['reviewer_id'] == 'other':
                assignment['route_id'] = route['id']
        result = self.assess()
        self.assertEqual((result['calls'], len(result['chains']), result['result']), (21, 2, 'fail'))
