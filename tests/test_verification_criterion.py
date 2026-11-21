"""Ruling 22 verification reads every declared start and its stored attempts."""

from contextlib import redirect_stdout
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci import route_capacity_profile as profile
from research_harness import bar_sources, cli, research_decisions as decisions, review_claude_transport as claude
from research_harness import review_composite as composite, review_failure_class as failures
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_session_v4 as session
from research_harness.artifacts import ArtifactStore
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.provenance import plugin_version
from test_research_claude_transport import clean_stream, completed
import test_research_problem_reviews as problem_fixtures
from test_review_route_capacity import build_route
import test_review_session_v4_pages as pages
import test_review_session_v4_turns as turns


class VerificationCriterionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = dict(review_route={'route': build_route()}, review_assignment={},
                            review_attempt={}, value_review={})
        self.declaration = dict(clone='synthetic-clone', route_ids=['route'], max_calls=60,
                                recorded_revision=1)

    def put(self, value):
        return self.artifacts.put(json.dumps(value).encode(), 'application/json')

    def add_chain(self, count, over=(), *, identifier='chain', recorded=True):
        plan = dict(policy='exactory-route-capacity-policy-v1', profile=dict(rows=[]),
            task=dict(request=dict(material_allowance=120000, state='fits',
                at_or_over_limit_share=0.02, levels=[dict(material_allowance=value)
                    for value in (120000, 60000, 30000, 15000)])))
        plan_ref = dict(protocol='exactory-route-capacity-plan-v1', digest=digest(plan),
                        artifact=self.put(plan))
        previous = None
        for number in range(count):
            key = identifier + '-' + str(number)
            assignment = dict(id=key, role='bar', route_id='route', reviewer_id=identifier,
                author_id='author', dossier_id=None, context=dict(prior_assignment_id=previous) if previous else {},
                recorded_revision=2 + 4 * number, capacity_plan=plan_ref,
                packet=self.put(dict(source_rules='exactory-independent-review-sources-v4')))
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

    def test_incomplete_diagnostic_output_is_counted_as_failure_without_becoming_recorded_answer(self):
        self.add_chain(19, (0,))
        attempt = self.records['review_attempt']['chain-0']
        attempt['output'] = self.put(dict(diagnostic='The provider stopped at the output limit.'))
        result = self.assess()
        self.assertEqual((result['result'], result['calls'], result['at_or_over_limit']), ('pass', 20, 1))
        self.assertFalse(result['chains'][0]['attempts'][0]['recorded'])

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
        plan['task']['request']['material_allowance'] = 120000
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


class StoredSessionVerificationTests(unittest.TestCase):
    """The real commands drive bar chains of the rules v4 on a route at max; a Claude stream stub answers."""
    read_packet = pages.SessionPagesTests.read_packet
    answer = pages.SessionPagesTests.answer
    start = pages.SessionPagesTests.start
    respond = pages.SessionPagesTests.respond
    add_sources = pages.SessionPagesTests.add_sources
    advance = pages.SessionPagesTests.advance
    fail_capacity = pages.SessionPagesTests.fail_capacity

    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.sizes = []
        self.sequence = 0
        # The borrowed helpers of the session tests answer through the same stub.
        for patcher in (patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)),
                        patch.object(pages, 'invoke_codex', side_effect=self.invoke_stub),
                        patch.object(turns, 'invoke_codex', side_effect=self.invoke_stub),
                        patch.object(pages, 'build_codex_response', side_effect=self.build_response)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.add_route('route')
        self.add_sources([1000])
        self.declaration = dict(clone='synthetic-clone', route_ids=['route'], max_calls=60,
                                recorded_revision=self.case.store.revision)

    def build_response(self, route, request, output, tokens=1200):
        """Run the real Claude transport on a clean stream; only the CLI process is replaced."""
        events = clean_stream(request, json.dumps(output), model=route['model'])
        events[-1]['usage']['output_tokens'] = tokens
        with patch.object(claude, '_cli', return_value=('fixture-claude', 'fixture-cli')), \
                patch.object(claude, '_run_owned', return_value=completed(events)):
            return claude.send_request(route, request)

    def add_route(self, identifier):
        self.case.mutate(protocol.record_route, dict(build_route(), id=identifier))
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(protocol.transport.PACKET_LABEL):])
            output = {key: packet[key] for key in ('allowed_control', 'evidence_control')}
            return self.build_response(route, request, dict(output, excluded_controls=[]))
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.probe_route, dict(id='probe-' + identifier, route_id=identifier), credential='test-only')

    def invoke_stub(self, case, assignment_id, output, attempt_id=None, tokens=1200):
        self.sequence += 1
        with patch.object(protocol.transport, 'send_request', side_effect=lambda route, request, credential:
                          self.build_response(route, request, output, tokens)):
            return self.case.mutate(protocol.invoke_assignment,
                dict(id=attempt_id or 'attempt-%d' % self.sequence, assignment_id=assignment_id), credential='test-only')

    def assign(self, reviewer, identifier, route='route', prior=None):
        context = dict(prior_assignment_id=prior['id']) if prior else self.case.deliver_supporting(reviewer)
        return self.case.mutate(protocol.record_assignment, dict(id=identifier, role='bar', route_id=route,
            dossier_id=None, reviewer_id=reviewer, author_id='author', context=context))['result']

    def finish_chain(self, current, calls=12):
        """Request the source once, pad with inventory pages, and record the bar on the last of the calls."""
        root = current['session_chain']['first_assignment_id']
        for number in range(80):
            records = self.case.read_records()
            members = {row['id'] for row in records['review_assignment'].values()
                       if row.get('session_chain', {}).get('first_assignment_id') == root}
            count = sum(row['assignment_id'] in members for row in records.get('review_attempt', {}).values())
            _, inventory, state = session.read_bound_session(self.case.artifacts, current)
            unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
            if 'session_turn' not in self.read_packet(current) and count >= calls - 1:
                turns.SessionTurnsTests.finish(self, current, [dict(ref=unit['handle'])])
                return current
            requests = [dict(id='inventory-%d' % number, kind='inventory_page', page=0)] if count < calls - 2 else []
            if (not any(row['unit'] == unit['handle'] for row in state['deliveries'])
                    and not any(row.get('unit') == unit['handle'] for row in state['pending_requests'])):
                requests = [dict(id='full-source', kind='unit', unit=unit['handle'])]
            self.invoke_stub(self.case, current['id'], dict(schema_version=1, stage='request', notes='Bounded notes.',
                excerpts=[], requests=requests, settled_requests=[]))
            checked = protocol.assignment_state(self.case.read_records(), self.case.artifacts, current['id'])
            self.assertTrue(checked['ready'], checked['obligations'])
            current = self.assign(current['reviewer_id'], root + '-turn-%d' % number, current['route_id'], current)
        self.fail('The fixture session did not complete')

    def finish_pages(self, current):
        for number in range(100):
            binding = session.read_session_binding(self.read_packet(current))
            self.invoke_stub(self.case, current['id'], self.answer(current))
            records = self.case.read_records()
            evaluation = Evaluation.of(records, self.case.artifacts)
            state = protocol.assignment_state(records, evaluation, current['id'])
            self.assertTrue(state['ready'], state['obligations'])
            if 'closing' in binding:
                output, record = composite.read_final_output(records, evaluation, current)
                self.assertIsNotNone(record)
                self.case.mutate(decisions.record_value_review,
                    dict(output, id='review-pages', dossier_id=None, assignment_id=current['id']))
                return current
            current = self.assign(current['reviewer_id'], 'final-%d' % self.case.store.revision,
                                  current['route_id'], current)
        self.fail('The final did not close')

    def pad_request(self, current, count):
        for number in range(count):
            self.invoke_stub(self.case, current['id'], dict(schema_version=1, stage='request', notes='Bounded notes.',
                excerpts=[], requests=[dict(id='padding-%d' % number, kind='inventory_page', page=0)], settled_requests=[]))
            current = self.assign(current['reviewer_id'], 'padding-%d-%d' % (self.sequence, number), current['route_id'], current)
        return current

    def assess(self):
        return profile.assess_verification_chain(self.case.read_records(), self.case.artifacts, self.declaration)

    def test_two_real_v4_bars_in_24_calls_report_actual_plan_and_routes(self):
        self.add_route('second-route')
        self.declaration['route_ids'].append('second-route')
        first = self.assign('first', 'first')
        self.invoke_stub(self.case, first['id'], {})
        replacement = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, first['id'])
        self.assertEqual(replacement['state'], 'replace', replacement)
        first = self.case.mutate(protocol.record_assignment, replacement['payload'])['result']
        self.finish_chain(first, 12)
        self.finish_chain(self.assign('second', 'second', 'second-route'), 12)
        result = self.assess()
        self.assertEqual((result['result'], result['calls'], len(result['chains'])), ('pass', 24, 2))
        self.assertEqual(result['chains'][0]['attempts'][0]['position'], result['chains'][0]['attempts'][1]['position'])
        self.assertEqual(result['plugin_version'], plugin_version())
        self.assertEqual(set(result['routes']), {'route', 'second-route'})
        self.assertEqual(result['routes']['route']['digest'], digest(self.case.read_records()['review_route']['route']))
        self.assertEqual(len(result['policy_digest']), 64)
        for chain in result['chains']:
            self.assertIn(chain['initial_profile']['request']['state'], ('unmeasured', 'fits', 'over_limit_expected'))
            self.assertIn('share_at_or_over_limit', chain['initial_profile']['request'])
        self.declaration['route_ids'].remove('second-route')
        self.assertEqual(self.assess()['reasons'], ['verification_route_changed'])

    def test_real_request_limit_uses_next_level_in_twenty_calls(self):
        turns.SessionTurnsTests.install_measured_profile(self)
        current = self.assign('first', 'start')
        self.invoke_stub(self.case, current['id'], {}, tokens=128000)
        step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
        self.finish_chain(current, 20)
        result = self.assess()
        self.assertEqual((result['result'], result['calls'], result['at_or_over_limit']), ('pass', 20, 1),
                         result['chains'][0]['reasons'])
        attempts = result['chains'][0]['attempts']
        self.assertEqual((attempts[0]['level'], attempts[1]['level']), (120000, 60000))
        self.assertEqual(attempts[0]['position'], attempts[1]['position'])

    def test_real_floor_one_extra_run_is_one_failure_and_second_fails_position(self):
        route = self.case.read_records()['review_route']['route']
        initial = capacity.read_output_profile({}, route, priors=[])
        with patch.object(capacity, 'read_output_profile', return_value=initial):
            current = self.assign('first', 'start')
        self.invoke_stub(self.case, current['id'], {}, tokens=128000)
        step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'run', step)
        self.finish_chain(current, 20)
        result = self.assess()
        self.assertEqual((result['result'], result['calls'], result['at_or_over_limit']), ('pass', 20, 1),
                         result['chains'][0]['reasons'])
        with patch.object(capacity, 'read_output_profile', return_value=initial):
            current = self.assign('second', 'second-floor')
        self.invoke_stub(self.case, current['id'], {}, tokens=128000)
        self.invoke_stub(self.case, current['id'], {}, tokens=128000)
        chain = next(row for row in self.assess()['chains'] if row['id'] == current['id'])
        self.assertIn('position_output_count_above_bound', chain['reasons'])
        self.assertEqual(self.assess()['result'], 'fail')
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            cli.main(['review-series', '--workspace', str(self.case.store.root), '--assignment', current['id']],
                     render_views=lambda workspace: None)
        self.assertEqual(exited.exception.code, 3)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['code'], 'review_output_over_cap')
        chain = next(row for row in self.assess()['chains'] if row['id'] == current['id'])
        self.assertIn('size_decision_stop', chain['reasons'])

    def split_page_twice(self, padding):
        current = self.pad_request(self.start(targets=9), padding)
        for _ in range(2):
            step = self.fail_capacity(current)
            self.assertEqual(step['state'], 'replace', step)
            current = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
        self.finish_pages(current)
        return self.assess()

    def test_real_page_split_and_nested_split_keep_new_positions(self):
        result = self.split_page_twice(15)
        self.assertEqual(result['result'], 'pass', result['chains'][0]['reasons'])
        rows = [row for row in result['chains'][0]['attempts'] if row['at_or_over_limit']]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['position'], rows[1]['position'])
        self.assertEqual(len(result['chains'][0]['positions']), 2)
        self.assertEqual(result['calls'], len(self.case.read_records()['review_attempt']))
        records = self.case.read_records()
        review = records['value_review']['review-pages']
        self.assertEqual(result['chains'][0]['recorded_bar'], review['id'])
        review.pop('composite')
        self.assertEqual(profile.assess_verification_chain(records, self.case.artifacts, self.declaration)['result'],
                         'incomplete')

    def test_real_parent_and_child_page_failures_both_count_in_the_bounds(self):
        result = self.split_page_twice(0)
        chain = result['chains'][0]
        self.assertLessEqual(result['calls'], 20)
        self.assertEqual((chain['at_or_over_limit'], len(chain['positions']), chain['bound']), (2, 2, 1))
        self.assertEqual(chain['reasons'], ['chain_output_share_above_bound'])
        self.assertEqual((result['result'], result['reasons']), ('fail', ['verification_output_share_above_bound']))

    def test_real_page_answer_at_the_limit_is_replaced_and_not_a_recorded_answer(self):
        current = self.pad_request(self.start(targets=9), 15)
        self.invoke_stub(self.case, current['id'], self.answer(current), tokens=128000)
        step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        self.finish_pages(self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result'])
        result = self.assess()
        self.assertEqual(result['result'], 'pass', result['chains'][0]['reasons'])
        limit = next(row for row in result['chains'][0]['attempts'] if row['at_or_over_limit'])
        self.assertEqual((limit['outcome'], limit['recorded']), ('output_over_cap', False))
        self.assertNotIn(limit['id'], self.case.read_records().get('review_output_refusal', {}))

    def test_real_closing_split_gives_each_condensing_turn_a_position(self):
        current = self.pad_request(self.start(targets=9), 15)
        while 'closing' not in self.read_packet(current).get('session_final', {}):
            current = self.advance(current)
        step = self.fail_capacity(current)
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
        self.assertIn('session_combine', self.read_packet(current))
        self.finish_pages(current)
        result = self.assess()
        self.assertEqual(result['result'], 'pass', result['chains'][0]['reasons'])
        combines = [row for row in result['chains'][0]['attempts'] if row['kind'] == 'combine']
        self.assertGreaterEqual(len(combines), 2)
        self.assertEqual(len({row['position'] for row in combines}), len(combines))
        self.assertIn('combine', result['chains'][0]['initial_profile'])

    def test_real_v3_fails_and_unfinished_start_counts(self):
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            old = self.assign('first', 'legacy')
        self.assertEqual(self.read_packet(old)['source_rules'], bar_sources.PROTOCOL_V3)
        result = self.assess()
        self.assertIn('session_rules_are_not_v4', result['chains'][0]['reasons'])
        self.assertEqual(result['result'], 'fail')
        # A second reviewer starts under the rules v4 and never reaches a recorded bar.
        self.respond(self.assign('second', 'unfinished'))
        result = self.assess()
        self.assertEqual(len(result['chains']), 2)
        self.assertEqual([chain['calls'] for chain in result['chains']], [0, 1])
