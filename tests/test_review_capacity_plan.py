"""Pinned budgets for future chain builders, exercised through fixture assignments."""
import copy
import json
import random
import unittest
from unittest.mock import patch

from research_harness import review_route_capacity as capacity, review_preflight, review_protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_route_capacity import build_route


def build_inputs(targets=10):
    return dict(frames={kind: {'instructions': 2000, 'references': 1000}
                        for kind in ('reading', 'final')},
                inventory=[dict(handle='u1', kind='fulltext', characters=450000)],
                targets=['t' + str(index) for index in range(targets)], carried_budget=450000)


def build_plan(**changes):
    route = build_route(effort='xhigh')
    inputs = build_inputs()
    inputs.update(changes)
    return capacity.plan_chain_capacity(route, capacity.read_output_profile({}, route), **inputs)


class DivideRoomTests(unittest.TestCase):
    def test_generated_admitted_lists_honour_every_minimum_and_cap(self):
        randomizer = random.Random(42)
        for _ in range(300):
            parts = []
            for index in range(randomizer.randrange(1, 50)):
                minimum = randomizer.randrange(1000)
                preferred = minimum + randomizer.randrange(1000)
                parts.append(dict(id=str(index), minimum=minimum, preferred=preferred,
                                  maximum=preferred + randomizer.randrange(1000)))
            room = sum(part['minimum'] for part in parts) + randomizer.randrange(20000)
            before = copy.deepcopy(parts)
            result = capacity.divide_room(room, parts)
            self.assertEqual(parts, before)
            self.assertEqual(sum(part['allowance'] for part in result['parts']) + result['fill'], room)
            for part in result['parts']:
                self.assertLessEqual(part['minimum'], part['allowance'])
                self.assertLessEqual(part['allowance'], part['maximum'])

    def test_weighted_room_saturates_caps_and_retains_fill(self):
        parts = [dict(id='a', minimum=10, preferred=20, maximum=30),
                 dict(id='b', minimum=10, preferred=60, maximum=200)]
        self.assertEqual([p['allowance'] for p in capacity.divide_room(60, parts)['parts']], [20, 40])
        self.assertEqual(capacity.divide_room(250, parts)['fill'], 20)
        self.assertEqual(capacity.divide_room(9, [])['fill'], 9)

    def test_finding_three_does_not_charge_the_budget_per_turn(self):
        inventory = [dict(handle='u' + str(index), kind='fulltext', characters=120000) for index in range(100)]
        plan = build_plan(inventory=inventory)
        route = build_route(effort='xhigh')
        forecast = capacity.describe_capacity_forecast(plan, route, capacity.read_output_profile({}, route))
        self.assertEqual(forecast['by_kind']['reading']['turns'], 100)
        allocation = plan['allocation']
        self.assertEqual(allocation['carried']['room'], 450000)
        self.assertEqual(len(allocation['targets']), 10)
        self.assertTrue(all(row['excerpts'] >= 30000 for row in allocation['targets']))
        # 100 turns never enter divide_room's inputs; all excerpts draw from one pool.
        self.assertLessEqual(sum(row['excerpts'] for row in allocation['targets']) + allocation['notes'], 450000)
        self.assertNotIn('planned_turns', allocation)


class CapacityPlanTests(unittest.TestCase):
    def test_plan_pins_every_input_and_counts_carried_state_and_notice(self):
        from research_harness.review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
        plan = build_plan()
        self.assertEqual(set(plan), {'protocol', 'policy', 'route_class', 'limits', 'measure', 'profile',
                                     'frame', 'inventory', 'targets', 'allocation', 'task'})
        self.assertEqual(plan['profile']['digest'], digest(plan['profile']['rows']))
        for kind, task in plan['task'].items():
            self.assertEqual(task['task_characters'], plan['frame'][kind]['bound'] +
                             task['material_allowance'] + task['carried_characters'] + BOUNDED_NOTICE_CHARACTERS)
            self.assertEqual(task['notice_characters'], BOUNDED_NOTICE_CHARACTERS)
            self.assertEqual(plan['allocation']['rooms'][kind], plan['limits']['input_text_characters']['value'] -
                             plan['frame'][kind]['bound'] - BOUNDED_NOTICE_CHARACTERS)
        self.assertGreater(plan['task']['reading']['carried_characters'], 0)
        self.assertTrue(plan['measure']['count_carried_state'])
        self.assertEqual(plan['profile']['states'], {kind: row['state'] for kind, row in plan['task'].items()})

    def test_carried_allowance_rows_include_native_serialization_cost(self):
        route = build_route(effort='xhigh')
        plan = build_plan()
        rows = plan['allocation']['targets']
        serialized = capacity.measure_capacity_value(route, rows)
        self.assertGreater(serialized, len(json.dumps(rows, sort_keys=True, ensure_ascii=False)))
        self.assertEqual(plan['task']['reading']['carried_characters'], plan['allocation']['notes'] + serialized)

    def test_a5_five_thousand_targets_refuse_before_any_call(self):
        with patch('research_harness.review_transport.send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                build_plan(targets=build_inputs(5000)['targets'])
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertGreater(caught.exception.details['minimum'], caught.exception.details['room'])
        send.assert_not_called()

    def test_q15_forecast_changes_without_rewriting_pinned_plan(self):
        plan = build_plan()
        before = copy.deepcopy(plan)
        route = build_route(effort='xhigh')
        profile = capacity.read_output_profile({}, route)
        first = capacity.describe_capacity_forecast(plan, route, profile)
        altered = copy.deepcopy(profile)
        for row in altered['rows']:
            row['at_or_over_limit'] += 1
            row['attempts'] += 1
        altered['digest'] = digest(altered['rows'])
        second = capacity.describe_capacity_forecast(plan, route, altered)
        self.assertNotEqual(first, second)
        self.assertEqual(plan, before)
        self.assertGreaterEqual(second['by_kind']['reading']['expected_calls'], second['by_kind']['reading']['turns'])
        self.assertTrue(first['by_kind']['reading']['turns'] > 1)

    def test_exact_request_above_pinned_bound_is_harness_defect(self):
        route = build_route(effort='xhigh')
        plan = build_plan()
        for kind, task in plan['task'].items():
            observations = []
            for _ in task['levels']:
                limits = capacity.derive_plan_task_limits(plan, kind, observations)
                bound = limits['request_bound']['text_characters']
                allowed = {'instructions': 'Read', 'input': [{'role': 'user', 'content': ''}]}
                overhead = capacity.measure_request(route, allowed)['text_characters']
                allowed['input'][0]['content'] = 'x' * (bound - overhead)
                fitting = review_preflight.build_report({}, None, route, 'standalone', 'Read', {}, allowed,
                    capacity_plan=plan, turn_kind=kind, observations=observations)
                self.assertTrue(review_preflight.check_request(fitting, allowed)['plan_comparison']['fits'])
                observations.append(dict(material_allowance=limits['material_allowance'], output_tokens=128000))
        request = {'instructions': 'Review', 'input': [{'role': 'user', 'content': 'x' * 200000}]}
        report = review_preflight.build_report({}, None, route, 'standalone', 'Review', {}, request,
                                                capacity_plan=plan, turn_kind='reading')
        self.assertEqual(report['protocol'], 'exactory-review-preflight-v2')
        self.assertIn('tokens_upper', report['request_measure'])
        with self.assertRaises(ResearchError) as caught:
            review_preflight.check_request(report, request)
        self.assertEqual(caught.exception.code, 'review_route_capacity_plan_violated')
        from research_harness.stop_classes import classify_stop, STOP_REGISTRY
        self.assertEqual(classify_stop(caught.exception.code), 'harness_defect')
        self.assertTrue(STOP_REGISTRY[caught.exception.code]['proof'])

    def test_frame_is_bounded_and_inventory_counts_unique_bodies(self):
        inputs = build_inputs()
        inputs['frames']['reading']['large_field'] = 1500000
        inputs['inventory'].append(dict(inputs['inventory'][0], handle='u2', sha256='a' * 64))
        inputs['inventory'][0]['sha256'] = 'a' * 64
        plan = build_plan(**inputs)
        frame = plan['frame']['reading']
        self.assertLess(frame['bound'], frame['characters'])
        self.assertIn('large_field', frame['unit_fields'])
        self.assertEqual(plan['inventory']['units'], 1)


class CapacityAssignmentCase(unittest.TestCase):
    def setUp(self):
        from test_research_review_protocol import ReviewProtocolTests
        self.case = ReviewProtocolTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.route()
        self.evidence = self.case.artifacts.put(b'Synthetic primary evidence.', 'text/plain')
        self.original_builder = review_protocol.build_packet

    def build_payload(self, identifier='planned'):
        return dict(id=identifier, route_id='route', dossier_id=None, role='standalone',
                    reviewer_id='reviewer', author_id='author', context={'artifact_refs': [self.evidence]})

    def build_fixture_packet(self, declaration):
        def build(*args, **kwargs):
            packet = self.original_builder(*args, **kwargs)
            packet[capacity.CAPACITY_REQUEST_KEY] = declaration
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        return build

    def invoke_check(self, payload):
        from research_harness import cli
        path = self.case.root.parent / 'plan-input.json'
        path.write_text(json.dumps(payload))
        args = cli.build_parser().parse_args(['review-assignment', '--check', '--workspace', str(self.case.root),
            '--file', str(path), '--expected-revision', str(self.case.store.revision), '--request-id', 'capacity-check'])
        return cli.run(args)

class StoredCapacityPlanTests(CapacityAssignmentCase):
    def test_check_returns_complete_plan_then_stored_digest_survives_new_priors_and_attempts(self):
        declaration = dict(build_inputs(1), kind='reading')
        profile = capacity.read_output_profile({}, self.case.records()['review_route']['route'])
        row = capacity._create_profile_row('reading', 120000)
        row.update(attempts=100, completed_attempts=100, largest_thinking_tokens=20,
                   largest_output_tokens=100, thinking_attempts=100)
        profile['rows'] = [row]
        profile['digest'] = digest(profile['rows'])
        before = self.case.store.snapshot()
        files = {str(path): path.read_bytes() for path in self.case.root.rglob('*') if path.is_file()}
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)), \
                patch.object(capacity, 'read_output_profile', return_value=profile):
            checked = self.invoke_check(self.build_payload())
            self.assertIn('capacity_plan', checked)
            self.assertEqual(checked['recorded'], False)
            self.assertEqual(self.case.store.snapshot(), before)
            self.assertEqual({str(path): path.read_bytes() for path in self.case.root.rglob('*') if path.is_file()}, files)
            assigned = self.case.mutate(review_protocol.record_assignment, self.build_payload())['result']
        pinned = capacity.read_chain_capacity_plan(self.case.artifacts, assigned)
        self.assertEqual(pinned, checked['capacity_plan'])
        reading = pinned['task']['reading']
        self.assertGreater(reading['carried_characters'], 0)
        self.assertEqual(reading['task_characters'], pinned['frame']['reading']['bound'] +
                         reading['material_allowance'] + reading['carried_characters'] + reading['notice_characters'])
        self.assertEqual(assigned['capacity_plan']['digest'], digest(pinned))
        packet = review_protocol._read_json(self.case.artifacts, assigned['packet'])
        self.assertNotIn(capacity.CAPACITY_REQUEST_KEY, packet)
        self.assertNotIn('capacity_plan', packet)
        original_request = review_protocol.build_assignment_request(self.case.records(), self.case.artifacts, assigned)
        altered = copy.deepcopy(profile)
        altered['rows'][0].update(attempts=150, at_or_over_limit=50)
        altered['digest'] = digest(altered['rows'])
        altered['store_attempts'] = 50
        declaration = dict(kind='reading', plan=assigned['capacity_plan'])
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)), \
                patch.object(capacity, 'read_output_profile', return_value=altered):
            next_check = self.invoke_check(self.build_payload('next'))
            self.assertEqual(digest(next_check['capacity_plan']), assigned['capacity_plan']['digest'])
            self.assertNotEqual(next_check['capacity_forecast'], checked['capacity_forecast'])
            self.assertEqual(capacity.read_chain_capacity_plan(self.case.artifacts, assigned), pinned)
            report = review_preflight.build_assignment_report(self.case.records(), self.case.artifacts, assigned)
        self.assertEqual(report['protocol'], 'exactory-review-preflight-v2')
        self.assertEqual(report['plan_comparison']['digest'], assigned['capacity_plan']['digest'])
        self.assertEqual(original_request,
                         review_protocol.build_assignment_request(self.case.records(), self.case.artifacts, assigned))
        self.assertNotIn('capacity_forecast', self.case.records()['review_assignment']['planned'])

    def test_a5_check_five_thousand_targets_refuses_before_recording_or_calling(self):
        declaration = dict(build_inputs(5000), kind='reading')
        before = self.case.store.snapshot()
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)), \
                patch('research_harness.review_transport.send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.invoke_check(self.build_payload())
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(self.case.store.snapshot(), before)
        send.assert_not_called()

    def test_stored_legacy_assignment_request_prompt_packet_and_v1_report_keep_their_bytes(self):
        assigned = self.case.assign()['result']
        records = self.case.records()
        before = copy.deepcopy(assigned)
        request = review_protocol.build_assignment_request(records, self.case.artifacts, assigned)
        packet_bytes = self.case.artifacts.read(assigned['packet'])
        build_plan()
        report = review_preflight.build_assignment_report(records, self.case.artifacts, assigned, request)
        with patch.object(capacity, 'read_output_profile', side_effect=AssertionError('Legacy replay must not plan')):
            repeated = review_preflight.build_assignment_report(records, self.case.artifacts, assigned, request)
            self.assertEqual(report, repeated)
            self.assertEqual(request, review_protocol.build_assignment_request(records, self.case.artifacts, assigned))
        self.assertEqual(report['protocol'], 'exactory-review-preflight-v1')
        self.assertEqual(assigned, before)
        self.assertNotIn('capacity_plan', assigned)
        self.assertEqual(self.case.artifacts.read(assigned['packet']), packet_bytes)

    def test_stop_rows_supply_route_payload_and_defect_inspection(self):
        from research_harness import review_plan_steps
        from research_harness.stop_classes import describe_terminal
        assignment = self.case.assign()['result']
        for code in ('review_route_capacity_below_floor', 'review_route_capacity_plan_violated'):
            plan = review_plan_steps.complete_plan(self.case.records(), self.case.artifacts, assignment,
                                                  describe_terminal(code))
            self.assertEqual(plan['command'], 'review-route' if code.endswith('below_floor') else 'gate-auto-pass')
            self.assertIn('step', plan['details'])


class PinnedPolicyTests(unittest.TestCase):
    def test_allowances_follow_pinned_levels_after_constants_and_priors_change(self):
        plan = build_plan()
        observations = [dict(material_allowance=120000, output_tokens=128000)]
        expected = capacity.derive_plan_task_limits(plan, 'reading', observations)
        with patch.object(capacity, 'describe_material_levels', side_effect=AssertionError('No current policy')), \
                patch.object(capacity, 'read_output_profile', side_effect=AssertionError('No current profile')):
            self.assertEqual(capacity.derive_plan_task_limits(plan, 'reading', observations), expected)
        self.assertEqual(expected['material_allowance'], 60000)
        self.assertEqual(expected['request_bound']['text_characters'], expected['task_characters'])

    def test_unmeasured_growth_stops_at_the_pinned_input_ceiling(self):
        route = build_route('openai_responses_v1', model='fixture-model', output=1000)
        plan = capacity.plan_chain_capacity(route, capacity.read_output_profile({}, route), **build_inputs(1))
        observations = []
        for _ in range(8):
            current = capacity.derive_plan_task_limits(plan, 'reading', observations)
            self.assertLessEqual(current['task_characters'], plan['limits']['input_text_characters']['value'])
            observations.append(dict(material_allowance=current['material_allowance'], output_tokens=0))
        self.assertEqual(current['material_allowance'], 60000)
        self.assertEqual(plan['task']['reading']['adaptation']['policy_ceiling'], 120000)

    def test_deriving_without_observations_preserves_every_start_field(self):
        route = build_route()
        profile = capacity.read_output_profile({}, route, priors=[])
        plan = capacity.plan_chain_capacity(route, profile, **build_inputs())
        for kind in plan['task']:
            self.assertEqual(capacity.derive_plan_task_limits(plan, kind), plan['task'][kind])

    def test_byte_measure_can_refuse_a_character_fitting_request(self):
        plan = build_plan()
        route = build_route(effort='xhigh')
        plan['task']['reading']['request_bound']['tokens_upper'] = 100
        request = {'instructions': 'Read', 'input': [{'role': 'user', 'content': '語' * 200}]}
        report = review_preflight.build_report({}, None, route, 'standalone', 'Read', {}, request,
                                               capacity_plan=plan, turn_kind='reading')
        self.assertEqual(report['request_measure']['measure'], 'bytes')
        self.assertTrue(report['plan_comparison']['sizes']['text_characters']['fits'])
        with self.assertRaises(ResearchError) as caught:
            review_preflight.check_request(report, request)
        self.assertEqual(caught.exception.code, 'review_route_capacity_plan_violated')

    def test_preflight_uses_the_bound_derived_from_authenticated_observations(self):
        route = build_route(effort='xhigh')
        plan = build_plan()
        request = {'instructions': 'Read', 'input': [{'role': 'user', 'content': 'x' * 150000}]}
        initial = review_preflight.build_report({}, None, route, 'standalone', 'Read', {}, request,
                                                capacity_plan=plan, turn_kind='reading')
        self.assertTrue(initial['plan_comparison']['fits'])
        adapted = review_preflight.build_report({}, None, route, 'standalone', 'Read', {}, request,
            capacity_plan=plan, turn_kind='reading',
            observations=[dict(material_allowance=120000, output_tokens=128000)])
        self.assertFalse(adapted['plan_comparison']['fits'])
        self.assertEqual(adapted['capacity_forecast']['by_kind']['reading']['material_allowance'], 60000)
        with self.assertRaises(ResearchError) as caught:
            review_preflight.check_request(adapted, request)
        self.assertEqual(caught.exception.code, 'review_route_capacity_plan_violated')

    def test_next_call_medians_use_only_the_selected_profile_range(self):
        route = build_route(effort='xhigh')
        records = {'review_route': {'route': route}, 'review_assignment': {'a': {'route_id': 'route'}},
                   'review_attempt': {}}
        for index, output in enumerate((100, 300, 200)):
            records['review_attempt'][str(index)] = dict(id=str(index), assignment_id='a', origin='instrumented',
                completed_generation=True, recorded_revision=index, status='completed',
                task=dict(kind='reading', characters=90000), usage=dict(output_tokens=output, wall_seconds=output / 10))
        records['review_attempt']['other'] = dict(records['review_attempt']['0'],
                                                 task=dict(kind='final', characters=90000))
        records['review_attempt']['manual'] = dict(records['review_attempt']['0'], origin='manual',
            route_id='route', usage=dict(output_tokens=9999, wall_seconds=9999))
        row = capacity.describe_next_capacity_call(records, route, dict(kind='reading', task_level=120000))
        self.assertEqual(row, dict(output_tokens=200, duration_seconds=20))

    def test_invalid_declarations_refuse_at_assignment_edge(self):
        inputs = dict(build_inputs(1), kind='reading')
        invalid = [dict(inputs, frames={}), dict(inputs, frames={'unknown': {}}),
                   dict(inputs, frames={'reading': {'instructions': -1}}),
                   dict(inputs, targets=['same', 'same']), dict(inputs, carried_budget=-1),
                   dict(inputs, inventory=[dict(handle='a', kind='text', characters=-1)]),
                   dict(inputs, targets=[dict(handle='a', gap_rows=-1)]),
                   dict(inputs, kind='not-planned')]
        route = build_route(effort='xhigh')
        for declaration in invalid:
            with self.subTest(declaration=declaration), self.assertRaises(ResearchError) as caught:
                capacity.prepare_assignment_capacity({}, None, route, declaration)
            self.assertEqual(caught.exception.code, 'invalid_input')


class CapacityDefectEvidenceTests(CapacityAssignmentCase):
    def test_plan_violation_retains_evidence_accepted_by_defect_reader(self):
        from research_harness import automatic_overrides
        declaration = dict(build_inputs(1), kind='reading')
        builder = self.build_fixture_packet(declaration)
        def oversized(*args, **kwargs):
            packet = builder(*args, **kwargs)
            packet['fixture_material'] = 'e' * 100000
            packet.pop('digest')
            packet['digest'] = digest(packet)
            return packet
        with patch.object(review_protocol, 'build_packet', side_effect=oversized):
            assignment = self.case.mutate(review_protocol.record_assignment, self.build_payload())['result']
        report = review_preflight.build_assignment_report(self.case.records(), self.case.artifacts, assignment)
        self.assertFalse(report['plan_comparison']['fits'])
        stop = automatic_overrides.record_series_stop(self.case.store, dict(
            state='stop', head_id='planned', code='review_route_capacity_plan_violated', progress={},
            details={'preflight': report}))
        self.assertEqual(stop['retry']['policy'], 'stored_capacity_plan_bound')
        self.assertEqual(stop['retry']['plan_digest'], assignment['capacity_plan']['digest'])
        self.assertEqual(automatic_overrides._trusted_stop(self.case.store, self.case.records(), stop['id']), stop)
        from research_harness.stop_classes import STOP_REGISTRY
        self.assertEqual(STOP_REGISTRY[stop['code']]['next'], STOP_REGISTRY['review_series_no_progress']['next'])
        from research_harness import review_series
        lines = []
        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), stop['plan'])):
            self.assertEqual(review_series.run_series(self.case.store, 'planned', emit=lines.append), 3)
        emitted = lines[-1]
        self.assertEqual(emitted['details']['step']['command'], 'gate-auto-pass')
        self.assertEqual(emitted['details']['step']['payload'], dict(stop_id=emitted['stop_id']))
        self.assertNotIn('stop_record_error', emitted)

    def test_a_request_inside_its_plan_cannot_supply_defect_evidence(self):
        from research_harness import automatic_overrides
        declaration = dict(build_inputs(1), kind='reading')
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)):
            self.case.mutate(review_protocol.record_assignment, self.build_payload())
        with self.assertRaises(ResearchError) as caught:
            automatic_overrides.record_series_stop(self.case.store, dict(
                state='stop', head_id='planned', code='review_route_capacity_plan_violated', progress={}, details={}))
        self.assertEqual(caught.exception.code, 'gate_override_stop_untrusted')


class ForecastAndFrameTests(unittest.TestCase):
    def test_q15_pages_pack_granted_entries_and_remaining_inputs_reduce_turns(self):
        route = build_route(effort='xhigh')
        plan = build_plan()
        profile = capacity.read_output_profile({}, route)
        row = capacity._create_profile_row('final', 120000)
        row.update(attempts=10, completed_attempts=10, largest_thinking_tokens=100, largest_output_tokens=100)
        profile['rows'] = [row]
        plan = capacity.plan_chain_capacity(route, profile, **build_inputs())
        forecast = capacity.describe_capacity_forecast(plan, route, profile)
        self.assertGreater(forecast['by_kind']['final']['turns'], 1)
        exhausted = capacity.describe_capacity_forecast(plan, route, profile,
                                                         remaining_inventory=[], remaining_targets=[])
        self.assertEqual(exhausted['turns'], 0)
        self.assertEqual(exhausted['expected_calls'], 0)
        self.assertEqual(exhausted['basis'], 'remaining_inputs')

    def test_unsafe_floor_counts_full_carried_reserve_and_refuses_impossible_range(self):
        route = build_route(effort='xhigh')
        profile = capacity.read_output_profile({}, route)
        row = capacity._create_profile_row('reading', 240000)
        row.update(attempts=100, completed_attempts=90, at_or_over_limit=10,
                   largest_thinking_tokens=100, largest_output_tokens=100)
        profile['rows'] = [row]
        inputs = build_inputs(1)
        inputs['frames']['reading'] = {'instructions': 100000}
        plan = capacity.plan_chain_capacity(route, profile, **inputs)
        floor = plan['task']['reading']['levels'][-1]
        self.assertLessEqual(floor['task_characters'], 120000)
        row['task_level'] = 120000
        with self.assertRaises(ResearchError) as caught:
            capacity.plan_chain_capacity(route, profile, **inputs)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')

    def test_duplicate_body_does_not_reserve_more_cards(self):
        inventory = [dict(handle='a', kind='fulltext', characters=450000, sha256='a' * 64)]
        single = build_plan(inventory=inventory)
        repeated = build_plan(inventory=inventory + [dict(inventory[0], handle='b')])
        self.assertEqual(single['allocation']['largest_card_count'], repeated['allocation']['largest_card_count'])
        self.assertEqual(single['allocation']['card_rate'], repeated['allocation']['card_rate'])
        empty = build_plan(inventory=[dict(handle='empty', kind='text', characters=0)])
        self.assertEqual(empty['allocation']['largest_card_count'], 1)

    def test_assignment_kind_selects_next_call_medians_from_probes_and_corrections(self):
        route = build_route(effort='xhigh')
        plan = build_plan()
        records = {'review_route': {'route': route}, 'review_route_probe': {}, 'review_output_correction': {}}
        for group, output in (('review_route_probe', 100), ('review_output_correction', 300)):
            records[group]['sample'] = dict(id='sample', route_id='route', origin='instrumented',
                completed_generation=True, recorded_revision=1, status='completed',
                task=dict(kind='final', characters=plan['task']['final']['task_characters']), usage=dict(output_tokens=output, wall_seconds=output / 10))
        profile = capacity.read_output_profile({}, route)
        profile['rows'] = []
        result = capacity.describe_capacity_forecast(plan, route, profile,
                                                     records=records, turn_kind='final')
        self.assertEqual(result['next_call'], dict(output_tokens=200, duration_seconds=20))


class EntryMeasurementTests(unittest.TestCase):
    def test_entry_constants_cover_stored_measurements(self):
        from pathlib import Path
        measured = json.loads((Path(__file__).parent / 'fixtures/capacity_plan_measurements.json').read_text())
        values = dict(excerpt_fields=capacity.EXCERPT_FIELDS_CHARACTERS,
                      cited_source=capacity.CITED_SOURCE_CHARACTERS, gap=capacity.GAP_ROW_CHARACTERS,
                      card=capacity.CARD_ENTRY_CHARACTERS, decision=capacity.DECISION_ROW_CHARACTERS)
        for kind, row in measured['entries'].items():
            self.assertEqual(values[kind], row['chosen'])
            if row['observed'] is not None:
                self.assertGreaterEqual(values[kind], row['observed'])


class FrozenAssignmentTests(unittest.TestCase):
    def test_pre_0853_assignment_rebuilds_its_request_after_a_plan_is_created(self):
        from test_review_failed_request_history import FailedRequestHistoryTests, read_cases
        fixture = FailedRequestHistoryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        frozen = read_cases('labelled-pre-fit-local')[1]
        records, invocation, kind = fixture.load_case(frozen)
        before = copy.deepcopy(records)
        expected = fixture.rebuild(records, invocation, kind)
        plan = build_plan()
        self.assertEqual(plan['protocol'], 'exactory-route-capacity-plan-v1')
        self.assertEqual(fixture.rebuild(records, invocation, kind), expected)
        self.assertEqual(records, before)
        self.assertEqual(expected['request_digest'], frozen['request_digest'])
        assignment = records['review_assignment']['assignment']
        report = review_preflight.build_report(records, fixture.artifacts, frozen['route'], assignment['role'],
            assignment['prompt'], frozen['packet'], json.loads(frozen['request']))
        self.assertEqual(report['protocol'], 'exactory-review-preflight-v1')
        self.assertNotIn('capacity_plan', assignment)


class AdaptedForecastTests(unittest.TestCase):
    def test_q15_authenticated_observations_price_the_current_pinned_allowance(self):
        route = build_route(effort='xhigh')
        plan = build_plan()
        forecast = capacity.describe_capacity_forecast(plan, route, capacity.read_output_profile({}, route),
            observations_by_kind={'reading': [dict(material_allowance=120000, output_tokens=128000)]})
        self.assertEqual(forecast['by_kind']['reading']['material_allowance'], 60000)
        self.assertEqual(forecast['by_kind']['reading']['turns'], 8)
        self.assertEqual(plan['task']['reading']['material_allowance'], 120000)


class CapacityReportTests(CapacityAssignmentCase):
    def test_receipt_status_and_series_line_expose_forecasts_outside_assignments(self):
        from research_harness import cli, review_series, review_continuation
        declaration = dict(build_inputs(1), kind='reading')
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)):
            receipt = self.case.mutate(review_protocol.record_assignment, self.build_payload())
        assignment = receipt['result']
        records = self.case.records()
        reported = cli.describe_review_receipt(self.case.store, 'review-assignment', self.build_payload(), receipt)
        self.assertEqual(digest(reported['capacity_plan']), assignment['capacity_plan']['digest'])
        self.assertIn('capacity_forecast', reported)
        with patch.object(review_continuation, 'list_chain_heads', return_value=['planned']):
            status = capacity.describe_open_route_reports(records, self.case.artifacts)
        self.assertIn('planned', status['route']['capacity_forecast'])
        plan = dict(state='stop', code='review_route_capacity_below_floor', head_id='planned',
                    progress={}, details={})
        lines = []
        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), plan)):
            self.assertEqual(review_series.run_series(self.case.store, 'planned', emit=lines.append), 3)
        self.assertIn('capacity_forecast', lines[-1])
        self.assertNotIn('capacity_forecast', self.case.records()['review_assignment']['planned'])


class ChangedStoreProfileTests(CapacityAssignmentCase):
    def test_recorded_attempts_change_the_forecast_and_preserve_the_stored_plan_and_allowances(self):
        declaration = dict(build_inputs(1), kind='reading')
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)):
            assignment = self.case.mutate(review_protocol.record_assignment, self.build_payload())['result']
        before = capacity.describe_assignment_capacity(self.case.records(), self.case.artifacts, assignment)
        allowance = capacity.derive_plan_task_limits(before['capacity_plan'], 'reading')
        for index in range(4):
            self.case.seed('review_attempt', 'sample-' + str(index), dict(
                id='sample-' + str(index), assignment_id='planned', origin='instrumented', status='completed',
                completed_generation=True, recorded_revision=self.case.store.revision + 1,
                task=dict(kind='reading', characters=allowance['task_characters']),
                usage=dict(thinking_tokens=20, output_tokens=100, input_tokens=100, sends=1),
                answer=dict(tokens=80)))
        after = capacity.describe_assignment_capacity(self.case.records(), self.case.artifacts, assignment)
        self.assertNotEqual(before['capacity_forecast'], after['capacity_forecast'])
        self.assertEqual(before['capacity_plan'], after['capacity_plan'])
        self.assertEqual(capacity.derive_plan_task_limits(after['capacity_plan'], 'reading'), allowance)
        self.assertEqual(digest(after['capacity_plan']), assignment['capacity_plan']['digest'])


class StoredPlanRefusalTests(CapacityAssignmentCase):
    def test_corrupt_reference_wrong_route_and_unplanned_kind_refuse(self):
        declaration = dict(build_inputs(1), kind='reading')
        with patch.object(review_protocol, 'build_packet', side_effect=self.build_fixture_packet(declaration)):
            assignment = self.case.mutate(review_protocol.record_assignment, self.build_payload())['result']
        reference = assignment['capacity_plan']
        with self.assertRaises(ResearchError) as caught:
            capacity.read_chain_capacity_plan(self.case.artifacts,
                dict(capacity_plan=dict(reference, digest='0' * 64)))
        self.assertEqual(caught.exception.code, 'artifact_corrupt')
        with self.assertRaises(ResearchError) as caught:
            capacity.prepare_assignment_capacity(self.case.records(), self.case.artifacts, build_route(),
                                                  dict(kind='reading', plan=reference))
        self.assertEqual(caught.exception.code, 'invalid_input')
        with self.assertRaises(ResearchError) as caught:
            capacity.prepare_assignment_capacity(self.case.records(), self.case.artifacts,
                self.case.records()['review_route']['route'], dict(kind='unknown', plan=reference))
        self.assertEqual(caught.exception.code, 'invalid_input')

    def test_route_without_the_floor_room_refuses(self):
        route = build_route(model='unlisted-model', output=150000)
        with self.assertRaises(ResearchError) as caught:
            capacity.plan_chain_capacity(route, capacity.read_output_profile({}, route), **build_inputs(1))
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')


if __name__ == '__main__':
    unittest.main()
