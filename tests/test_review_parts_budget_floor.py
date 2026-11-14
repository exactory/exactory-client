"""Continuation preserves the state room promised by parts v2 admission."""

import base64
import copy
import io
import json
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from codex_route_fixtures import bind_codex_response, build_codex_response
from research_harness import cli, report_views, review_parts as parts, review_transport as transport
from research_harness import review_protocol as protocol
from research_harness import review_parts_problem_result as result_parts
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
from review_parts_fixtures import (
    PartsResultCase, make_output, make_packet, measure_input_limit,
    measure_packet, observe, plan,
)


class BudgetFloorPlanningTests(unittest.TestCase):
    def test_repeated_downward_steps_stop_at_the_remaining_card_floor(self):
        packet = make_packet(20, 24000)
        input_limit = measure_input_limit(110000)
        observations = []
        allowances = []
        clamped = []
        for _ in range(30):
            turn = plan(packet, observations, input_limit=input_limit)
            header = turn[parts.KEY]
            clean = {key: value for key, value in turn.items()
                     if key != '_test_inventory'}
            self.assertNotIn('unit_allowance_floor', parts.render_json(clean))
            self.assertLessEqual(measure_packet(clean), input_limit)
            state = parts.build_state(observations)
            for name in ('cards', 'combinations', 'findings'):
                self.assertLessEqual(len(parts.render_json(state[name])),
                                     header['state_budget'][name])
            if header['kind'] == 'final':
                break
            output = make_output(turn)
            if header['kind'] == 'read':
                remaining = (turn['_test_inventory']['characters']
                             - sum(row['packet'][parts.KEY]['unit_characters']
                                   for row in observations
                                   if row['packet'][parts.KEY]['kind'] == 'read'))
                available = (header['state_budget']['cards']
                             - len(parts.render_json(state['cards'])))
                maximum_reads = available // 4000
                expected_floor = (remaining + maximum_reads - 1) // maximum_reads
                self.assertEqual(parts.describe_plan(turn).get('unit_allowance_floor'),
                                 expected_floor)
                allowance = header['plan']['unit_allowance']
                self.assertGreaterEqual(allowance, expected_floor)
                self.assertLessEqual(header['plan']['remaining_turns']['read']['estimate'],
                                     maximum_reads)
                if allowances and allowance == expected_floor:
                    clamped.append(allowance)
                allowances.append(allowance)
                padding = max(0, (header['output_allowance']
                                  - len(parts.render_json(output['cards']))
                                  - 1000) // len(output['cards']))
                for card in output['cards']:
                    card['summary'] += 's' * padding
            elif header['kind'] == 'combine':
                handles = [card['unit'] for card in turn[parts.STATE_KEY]['cards']]
                combinations = [dict(
                    units=list(dict.fromkeys(handles)), question='Check these together.',
                    view='The cards concern the same claim.',
                    read=[dict(unit=handles[0], item=True, start=0, end=100)])]
                output = make_output(turn, combinations)
            observations.append(observe(turn, output, output_tokens=100000))
        else:
            self.fail('Downward adaptation did not reach a final')
        self.assertGreater(allowances[0], allowances[1])
        self.assertGreater(allowances[1], allowances[2])
        self.assertTrue(clamped)
        self.assertEqual(header['plan']['units_remaining'], 0)
        self.assertTrue(state['combinations'])
        self.assertTrue(state['findings'])

    def test_admission_keeps_the_initial_read_allowance(self):
        packet = make_packet(3, 6000)
        input_limit = measure_input_limit(20000)
        turn = plan(packet, input_limit=input_limit)
        self.assertEqual(turn[parts.KEY]['kind'], 'read')
        self.assertEqual(turn[parts.KEY]['turn'], 1)
        self.assertGreaterEqual(turn[parts.KEY]['plan']['unit_allowance'],
                                turn[parts.KEY]['unit_characters'])

    def test_last_card_floor_stops_before_a_measured_partial_read_spends_it(self):
        packet = make_packet(10, 8000)
        input_limit = measure_input_limit(30000)
        observations = []
        for _ in range(10):
            try:
                turn = plan(packet, observations, input_limit=input_limit)
            except ResearchError as error:
                self.assertEqual(error.code, 'review_parts_budget_exhausted')
                self.assertEqual(error.details['maximum_read_turns'], 1)
                self.assertGreaterEqual(error.details['card_budget_remaining'], 4000)
                self.assertLess(error.details['unit_characters'],
                                error.details['unit_characters_remaining'])
                self.assertEqual(error.details['unit_allowance_floor'],
                                 error.details['unit_characters_remaining'])
                self.assertEqual(error.details['input_limit_characters'], input_limit)
                self.assertEqual(len(observations), 2)
                break
            self.assertEqual(turn[parts.KEY]['kind'], 'read')
            output = make_output(turn)
            padding = max(0, (turn[parts.KEY]['output_allowance']
                              - len(parts.render_json(output['cards'])) - 1000)
                          // len(output['cards']))
            for card in output['cards']:
                card['summary'] += 's' * padding
            observations.append(observe(turn, output, output_tokens=100000))
        else:
            self.fail('A partial read spent the last available card floor')

    def test_replacement_uses_the_clamped_allowance_for_a_whole_binary_body(self):
        packet = make_packet(20, 24000)
        data = b'\x00\xff' * 100
        reference = describe_artifact(data, 'application/octet-stream')
        packet['problem_evidence']['computations'] = [dict(artifact=reference)]
        packet['evidence'] = [dict(artifact=reference, encoding='base64',
                                   content=base64.b64encode(data).decode('ascii'))]
        input_limit = measure_input_limit(110000)
        turn = plan(packet, input_limit=input_limit,
                    replacement=dict(unit_characters=1, over_cap=True))
        self.assertEqual(turn[parts.KEY]['kind'], 'read')
        self.assertGreater(turn[parts.KEY]['plan']['unit_allowance'], len(data))

    def test_replacement_halving_stops_at_the_floor_before_initial_checks(self):
        packet = make_packet(20, 24000)
        input_limit = measure_input_limit(110000)
        first = plan(packet, input_limit=input_limit)
        replacement = plan(packet, input_limit=input_limit,
                           replacement=dict(unit_characters=1, over_cap=True))
        header = replacement[parts.KEY]
        remaining = replacement['_test_inventory']['characters']
        maximum_reads = (header['state_budget']['cards'] - 2) // 4000
        expected_floor = (remaining + maximum_reads - 1) // maximum_reads
        self.assertEqual(header['plan']['unit_allowance'], expected_floor)
        self.assertEqual(parts.describe_plan(replacement)['unit_allowance_floor'], expected_floor)
        self.assertEqual(first[parts.KEY]['turn'], header['turn'])

    def test_journey_without_downward_adaptation_keeps_the_original_plans(self):
        # Captured on 0.72.4. Measure a fixed request without planner metadata
        # so that both planners receive exactly the same route limit.
        packet = make_packet(3, 420000)
        probe = dict(make_packet(0), review_parts=dict(adapter='problem_result', kind='final'))
        input_limit = measure_packet(probe) + 900000
        expected = [
            ('read', 1, 0, 3, 600000, 80000, (3, 0, 0, 1)),
            ('read', 2, 1, 2, 600000, 80000, (2, 0, 0, 1)),
            ('read', 3, 2, 1, 600000, 80000, (1, 0, 0, 1)),
            ('combine', 4, 3, 0, 60069, 0, (0, 1, 0, 1)),
            ('final', 5, 3, 0, 60069, 0, (0, 0, 0, 1)),
        ]
        self.assert_journey_keeps_plans(packet, input_limit, expected)

    def test_input_limited_journey_keeps_plans_without_downward_adaptation(self):
        packet = make_packet(5)
        probe = dict(make_packet(0), review_parts=dict(adapter='problem_result', kind='final'))
        input_limit = measure_packet(probe) + 110000
        expected = [
            ('read', 1, 0, 5, 104081, 43529, (2, 0, 0, 1)),
            ('read', 2, 4, 1, 100475, 50138, (1, 0, 0, 1)),
            ('combine', 3, 5, 0, 19640, 0, (0, 1, 0, 1)),
            ('final', 4, 5, 0, 19640, 0, (0, 0, 0, 1)),
        ]
        # Packet and request hashes captured from the same 0.72.4 journey.
        hashes = [
            ('54cf4b7bc0fd03dd787b4aa379dc1ba61b41fa2daea1d5ee15ec564056f2b406',
             '78543a8c329318bd1cd46f6c0cff281e83f5ced31240bd99e77c20cbfdd8021a'),
            ('30181aa05e66509f6f0e87e2c6e3dd198d9ea70f97cebe1b0efa59f71c0f599d',
             '5d6ce256335fe3977f6eefda73f10ec994f9a9cb10536b6a7c67e9decb981da9'),
            ('8f97854be4a16c43ebc0af1f6058e4832f828fce5245b5b4ad9df97752e727e5',
             '24eb2446db65760dc9e381d5cdcbf64cdd3e6f2bdb840131c8a3b35165aa692b'),
            ('1a734b7f069dc3f55a46b787a09fa02b32119b9c22bcb907d66bf619410ab55c',
             '8059bff00d0050a24fb10625f1fb4f2e40fa66dcb1764ecffdcffc6f4bdb21c1'),
        ]
        self.assert_journey_keeps_plans(packet, input_limit, expected, hashes)

    @patch.dict(result_parts.ADAPTER, unit_rule=None)
    def assert_journey_keeps_plans(self, packet, input_limit, expected, hashes=None):
        # These journeys retain the inventory rule captured on 0.72.4.
        actual = []
        observations = []
        for _ in expected:
            turn = plan(packet, observations, input_limit=input_limit)
            self.assertNotIn('unit_allowance_floor', parts.render_json(turn))
            clean = {key: value for key, value in turn.items() if key != '_test_inventory'}
            if hashes:
                route = dict(adapter='codex_cli_v1', endpoint='codex://local', model='fixture',
                             configuration=dict(max_output_tokens=128000, timeout_seconds=5))
                prompt = parts.build_turn_prompt(
                    'result', dict(orchestration='problem_creation', excerpts=True), clean[parts.KEY])
                request = transport.request_body(route, prompt['text'], clean, [], order=())
                self.assertNotIn('unit_allowance_floor', parts.render_json(request))
                self.assertEqual((digest(clean), digest(request)), hashes[len(actual)])
            value = turn[parts.KEY]['plan']
            actual.append(tuple(value[key] for key in
                                ('kind', 'turn', 'units_carded', 'units_remaining',
                                 'unit_allowance', 'card_allowance'))
                          + (tuple(value['remaining_turns'][kind]['estimate']
                                   for kind in parts.KINDS),))
            if value['kind'] != 'final':
                observations.append(observe(turn))
        self.assertEqual(actual, expected)

    def test_historical_status_has_no_floor_and_reads_no_artifacts(self):
        turn = plan(make_packet(3, 6000), input_limit=measure_input_limit(20000))
        assignment = dict(id='old', recorded_revision=1,
                          review_parts=turn[parts.KEY], review_parts_plan=turn[parts.KEY]['plan'])
        series = dict(packets=[], complete=True, issues=dict(count=0, examples=[]))
        with patch('research_harness.artifacts.ArtifactStore.read',
                   side_effect=AssertionError('Status must not read an extra artifact')):
            progress = report_views.describe_parts_turn(assignment, {}, series)
        self.assertIsNone(progress['unit_allowance_floor'])

    def test_failed_floor_uses_recorded_measurements_and_tolerates_missing_fields(self):
        turn = plan(make_packet(20, 24000), input_limit=measure_input_limit(110000))
        header = turn[parts.KEY]
        remaining = header['unit_characters_remaining']
        available = header['card_budget_remaining']
        floor = (remaining + available // 4000 - 1) // (available // 4000)
        header['plan']['unit_allowance'] = floor
        header['plan'].pop('unit_allowance_floor', None)
        value = dict(role='result', route_id='route', reviewer_id='reviewer', author_id='author')
        failed = dict(value, id='failed', recorded_revision=1, slot_id='slot', packet={}, context={})
        attempt = dict(assignment_id='failed', usage=dict(output_tokens=128000),
                       error=dict(code='invalid_review_response'))
        records = dict(review_assignment=dict(failed=failed), review_attempt=dict(attempt=attempt))
        artifacts = unittest.mock.Mock()
        for missing in (None, 'unit_characters_remaining', 'card_budget_remaining', 'plan',
                        'state_budget'):
            with self.subTest(missing=missing):
                packet = copy.deepcopy(turn)
                if missing:
                    packet[parts.KEY].pop(missing)
                artifacts.read.return_value = parts.render_json(packet).encode()
                replacement = parts.find_failed_replacement(
                    records, artifacts, value, 'problem_result', None, 'slot', None, 128000)
                self.assertEqual(replacement['unit_characters'], header['unit_characters'])
                if missing in ('unit_characters_remaining', 'card_budget_remaining', 'plan'):
                    self.assertNotIn('unit_allowance_floor', replacement)
                else:
                    self.assertEqual(replacement.get('unit_allowance_floor'), floor)


class BudgetFloorReplacementTests(PartsResultCase):
    def test_floor_failure_plan_preserves_budget_measurements_and_classification(self):
        assignment, details = self.assert_floor_failure_stops()
        before = self.case.store.snapshot()
        plan = plan_next(before['records'], self.case.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_parts_budget_exhausted'))
        self.assertNotIn('command', plan)
        self.assertNotIn('payload', plan)
        actual = dict(plan['details'])
        self.assertEqual(actual.pop('outcome'), 'output_over_cap')
        self.assertEqual(actual.pop('reset_at'), None)
        attempt_id = actual.pop('attempt_id')
        self.assertEqual(attempt_id, 'overflow-2')
        self.assertEqual(actual.pop('failed_attempts'), 3)
        signal = actual.pop('signal')
        self.assertEqual(signal['source'], 'review_parts.find_failed_replacement')
        self.assertEqual(signal['value']['failed_assignment_id'], assignment['id'])
        self.assertEqual(actual, details)
        self.assertEqual(self.case.store.snapshot(), before)

    def test_series_ends_with_the_floor_budget_stop_without_another_call(self):
        assignment, details = self.assert_floor_failure_stops()
        before = self.case.store.snapshot()
        plan = plan_next(before['records'], self.case.artifacts, assignment['id'])
        output = io.StringIO()
        with patch.object(protocol.transport, 'send_request',
                          side_effect=AssertionError('A floor failure must not call a reviewer')), \
                redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            cli.main(['review-series', '--workspace', str(self.case.root),
                      '--assignment', assignment['id']], render_views=lambda workspace: None)
        self.assertEqual(exited.exception.code, 3)
        stop = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((stop['state'], stop['code']), ('stop', 'review_parts_budget_exhausted'))
        self.assertNotIn('command', stop)
        for key, value in details.items():
            self.assertEqual(stop['details'][key], value, key)
        after = self.case.store.snapshot()
        recorded_stop, = after['records']['review_series_stop'].values()
        self.assertEqual(stop, dict(plan, stop_id=recorded_stop['id'], stop_class='decision',
                                   provider_usage=dict(windows=[], warnings=[], planned_remaining_turns=0),
                                   revision_after_stop=after['revision']))
        self.assertEqual(recorded_stop['plan'], plan)
        self.assertEqual(recorded_stop['recorded_revision'], after['revision'])
        operations = dict(before['records']['literature_operation'])
        operations[recorded_stop['request_id']] = dict(operation='review.series_stop',
                                                      runtime=self.case.store.describe_runtime())
        self.assertEqual(after, dict(before, revision=before['revision'] + 1,
            records=dict(before['records'], review_series_stop={recorded_stop['id']: recorded_stop},
                         literature_operation=operations)))

    def test_over_cap_failure_at_the_floor_stops_before_recording_a_replacement(self):
        self.assert_floor_failure_stops()

    def test_continuing_floor_failure_stops_from_the_original_parent(self):
        self.assert_floor_failure_stops(continue_after_read=True)

    def test_incomplete_failure_at_the_floor_uses_the_existing_failure_predicate(self):
        self.assert_floor_failure_stops(incomplete_below_cap=True)

    def assert_floor_failure_stops(self, continue_after_read=False, incomplete_below_cap=False):
        self.oversized(count=20, characters=24000, room=110000)
        assignment = self.assign()
        prior = None
        if continue_after_read:
            self.invoke(assignment)
            prior = assignment['id']
            assignment = self.assign('continuing-read', prior)
        calls = []
        failed_tokens = self.output_tokens // 4 if incomplete_below_cap else self.output_tokens

        def fail_over_cap(route, request, credential):
            calls.append(copy.deepcopy(request))
            response = build_codex_response(route, request, {})
            if incomplete_below_cap:
                response['status'] = 'incomplete'
            else:
                response['output'][0]['content'][0]['text'] = '{'
            response['usage']['output_tokens'] = failed_tokens
            return bind_codex_response(route, request, response)

        for index in range(10):
            packet = self.packet(assignment)
            self.assertNotIn('unit_allowance_floor', parts.render_json(packet))
            header = packet[parts.KEY]
            floor = assignment['review_parts_plan'].get('unit_allowance_floor')
            self.assertIsInstance(floor, int)
            self.assertGreater(floor, 0)
            progress = cli._review_status(self.case.read_records(), self.case.artifacts)[
                'assignments'][assignment['id']]['review_parts']
            self.assertEqual(progress.get('unit_allowance_floor'), floor)
            attempt_id = 'overflow-' + str(index)
            with patch.object(protocol.transport, 'send_request', side_effect=fail_over_cap):
                protocol.invoke_assignment(
                    self.case.store, dict(id=attempt_id, assignment_id=assignment['id']),
                    credential='fixture', expected_revision=self.case.store.revision,
                    request_id=attempt_id)
            before = self.case.store.snapshot()
            if header['plan']['unit_allowance'] == floor:
                with self.assertRaises(ResearchError) as raised:
                    self.assign('refused-replacement', prior)
                self.assertEqual(raised.exception.code, 'review_parts_budget_exhausted')
                details = raised.exception.details
                self.assertEqual(details['units_remaining'], header['plan']['units_remaining'])
                self.assertEqual(details['unit_characters_remaining'],
                                 header['unit_characters_remaining'])
                self.assertEqual(details['unit_allowance_floor'], floor)
                self.assertEqual(details['maximum_read_turns'],
                                 header['card_budget_remaining'] // 4000)
                self.assertEqual(details['card_budget_remaining'], header['card_budget_remaining'])
                self.assertEqual(details['state_budget'], header['state_budget'])
                self.assertEqual(details['output_tokens'], failed_tokens)
                self.assertEqual(details['max_output_tokens'], self.output_tokens)
                self.assertEqual(details['failed_assignment_id'], assignment['id'])
                self.assertEqual(self.case.store.snapshot(), before)
                self.assertEqual(len(calls), index + 1)
                return assignment, details
            following = self.assign('replacement-' + str(index), prior)
            expected = max(following['review_parts_plan']['unit_allowance_floor'],
                           header['unit_characters'] // 2)
            self.assertEqual(following['review_parts_plan']['unit_allowance'], expected)
            assignment = following
        else:
            self.fail('Repeated replacements never reached the floor')

    output_tokens = 128000
