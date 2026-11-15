"""The shipped parts planner stays bounded as text grows; gaps remain explicit."""

import copy
import json
import unittest
from unittest.mock import patch

from ci import capacity_guard
from research_harness import review_parts as parts
from research_harness.errors import ResearchError
import review_parts_fixtures as fixtures
import reading_capacity_fixtures as reading_fixtures
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_route_capacity as capacity, review_transport
from research_harness.evidence import digest

PARTS_END_MESSAGE = 'A finite text did not finish its declared parts.'


class CapacityPropertyTests(unittest.TestCase):
    def test_text_sizes_to_thirty_route_limits_have_finite_fitting_turns(self):
        room = 80000
        limit = fixtures.measure_input_limit(room)
        gaps = capacity_guard.build_gap_table(capacity_guard.read_matrix())
        self.assertIn('Q17', gaps['gaps'])
        for numerator in (9, 10, 11, 30, 300):
            with self.subTest(tenths=numerator):
                packet = fixtures.make_packet(count=1, characters=limit * numerator // 10)
                observations = []
                for number in range(500):
                    try:
                        turn = fixtures.plan(packet, observations, room=room)
                    except ResearchError as error:
                        self.assertIn('A6', gaps['gaps'])
                        self.assertIn(error.code, ('review_parts_final_too_large', 'review_parts_inventory_too_large'))
                        break
                    clean = {key: value for key, value in turn.items() if key != '_test_inventory'}
                    self.assertLessEqual(fixtures.measure_packet(clean), limit)
                    if parts.find_kind(turn) == 'final':
                        break
                    observations.append(fixtures.observe(turn, output_tokens=1000))
                else:
                    self.fail(PARTS_END_MESSAGE)
                if observations and parts.find_kind(turn) == 'final':
                    inventory = turn['_test_inventory']
                    self.assertTrue(parts.covers_items([row['packet'] for row in observations],
                        packet['problem_evidence'], 'openness_searches', inventory))
                    self.assertEqual(parts.build_inventory(packet, 'problem_result', inventory), inventory)

    def test_ten_thousand_units_end_at_the_declared_inventory_boundary(self):
        packet = fixtures.make_packet(count=10000, characters=200)
        before = copy.deepcopy(packet)
        matrix = capacity_guard.read_matrix()
        self.assertIn('Q2', capacity_guard.build_gap_table(matrix)['gaps'])
        with self.assertRaises(ResearchError) as caught:
            fixtures.plan(packet, room=80000)
        self.assertIn(caught.exception.code, ('review_parts_frame_too_large',
            'review_parts_inventory_too_large', 'review_parts_final_too_large'))
        self.assertEqual(packet, before)


class ReadingCapacityPropertyTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('capacity-guard-2', capacity_guard.read_matrix()['installed_units'])

    def drive_inventory(self, route, built, native=None, shape_name=None):
        artifacts = self.artifacts
        native = native or reading_fixtures.build_native()
        logical = dict(protocol='exactory-independent-review-v1', role='native_math', dossier_id=None,
                       reviewer_id=native['reviewer'], native=native)
        with patch.object(v7, 'build_inventory', return_value=built):
            admission = v7.check_v7_admission({}, artifacts, route, native)
        if admission['state'] == 'stop':
            if admission.get('code') == v7.VISUAL_TEXT_ROUTE_CODE:
                self.assertEqual(route['adapter'], 'codex_cli_visual_v1')
                self.assertEqual(admission['reason'], 'text_route_required')
                following = reading_fixtures.execute_visual_text_answer(self, route, admission['details'], built=built)
                self.assertEqual(following['capacity_plan_kind'], 'reading')
                return admission
            self.assertIn(admission['reason'], ('below_floor', 'final_needs_pages'))
            if admission['reason'] == 'final_needs_pages':
                declared = capacity_guard.read_matrix()['reading_property_ends']
                self.assertIn(shape_name, declared, 'A final-pages stop must name its explicit gap.')
                self.assertEqual(declared[shape_name]['closing_unit'], 'final-pages')
            return admission
        packet, chain = v7.prepare_first_assignment({}, artifacts, dict(id='property-chain'), route,
                                                   logical, [], admission=admission)
        plan = capacity.read_chain_capacity_plan(artifacts, chain)
        inventory = json.loads(artifacts.read(chain['inventory']))
        state = json.loads(artifacts.read(chain['state']))
        state['carried_readings'] = [row['handle'] for row in inventory['units']
                                    if row['kind'] == 'carried_reading']
        turns = []
        # Each successful reading removes at least one character. This is a
        # termination bound derived from the fixture, not a fixed turn ceiling.
        maximum = sum(row['characters'] for row in inventory['material']) + len(inventory['material']) + 2
        delivered = {}
        for number in range(1, maximum):
            state['carried_readings'] = [row['handle'] for row in inventory['units']
                                        if row['kind'] == 'carried_reading']
            kind = 'reading' if v7._find_remaining_ranges(inventory, state) else 'final'
            limits = capacity.derive_plan_task_limits(plan, kind)
            packet = v7.build_turn_packet(artifacts, route, logical, 'property-chain', number,
                                         inventory, state, plan, limits, kind)
            reading_fixtures.assert_turn_fits(self, route, packet, plan, kind)
            if kind == 'final':
                for body in inventory['material']:
                    self.assertEqual(delivered[body['handle']], body['characters'])
                self.assertEqual(state['inventory_digest'], digest(inventory))
                return dict(state='final', turns=number, plan=plan, packet=packet)
            self.assertEqual(packet['reading_turn']['capacity']['material_allowance'], limits['material_allowance'])
            now = packet['reading_turn']['plan']['now']
            self.assertTrue(now, 'An admitted finite inventory must make progress.')
            self.assertLessEqual(capacity.measure_capacity_value(route, now) +
                capacity.measure_capacity_value(route, packet['evidence']), limits['material_allowance'])
            for row in now:
                self.assertGreater(row['end'], row['start'])
                self.assertEqual(row['start'], delivered.get(row['handle'], 0))
                delivered[row['handle']] = row['end']
            output = reading_fixtures.build_reading_answer(packet)
            v7.validate_turn_output(packet, output)
            projected = v7.project_own_turn(artifacts, route, packet, output,
                'turn-%d' % number, 'attempt-%d' % number, state=state, inventory=inventory)
            turns.append(projected)
            state = v7.build_chain_state(inventory, turns, route=route, plan=plan)['state']
        self.fail('An admitted finite inventory did not finish.')

    def test_v7_every_adapter_and_profile_class_delivers_scaled_bodies(self):
        from test_review_output_profile import build_row
        adapters = (review_transport.ADAPTER,) + tuple(review_transport.LOCAL_ADAPTERS)
        for adapter in adapters:
            for category in ('fits', 'over_limit_expected', 'unlisted'):
                route = reading_fixtures.build_reading_route(adapter, category)
                priors = ([build_row(level=None, count=8, thinking=1000),
                           build_row(kind='final', level=None, count=8, thinking=1000)]
                          if category == 'fits' else
                          [build_row(level=None, count=8, over=1, thinking=1000),
                           build_row(kind='final', level=None, count=8, over=1, thinking=1000)]
                          if category == 'over_limit_expected' else [])
                with patch.object(capacity, 'read_profile_priors', return_value=priors):
                    self.artifacts = reading_fixtures.MemoryArtifacts()
                    room = capacity.describe_route_capacity(route)['input_text_characters']['value']
                    for numerator in (9, 10, 11, 30, 300):
                        with self.subTest(adapter=adapter, category=category, tenths=numerator):
                            built = reading_fixtures.build_shape_inventory(self.artifacts,
                                characters=max(200, room * numerator // 10))
                            result = self.drive_inventory(route, built)
                            print('READING_PROPERTY_END ' + json.dumps(dict(adapter=adapter, category=category,
                                tenths=numerator, end=result['state'], reason=result.get('reason'),
                                turns=result.get('turns'))), flush=True)
                            self.assertIn(result['state'], ('stop', 'final'))
                            if category == 'fits' and result['state'] == 'final':
                                self.assertEqual(result['plan']['task']['reading']['state'], 'fits')
                            if category == 'over_limit_expected' and result['state'] == 'final':
                                self.assertEqual(result['plan']['task']['reading']['state'], 'over_limit_expected')

    def test_v7_ten_thousand_units_and_non_ascii_have_declared_ends(self):
        route = reading_fixtures.build_reading_route('codex_cli_v1', 'over_limit_expected')
        for count, characters, non_ascii in ((10000, 200, False), (1, 200000, True)):
            with self.subTest(count=count, non_ascii=non_ascii):
                self.artifacts = reading_fixtures.MemoryArtifacts()
                built = reading_fixtures.build_shape_inventory(self.artifacts, count=count,
                    characters=characters, non_ascii=non_ascii)
                result = self.drive_inventory(route, built)
                if non_ascii:
                    self.assertIn(result['state'], ('final', 'stop'))
                    if result['state'] == 'stop':
                        self.assertEqual(result['code'], 'review_route_capacity_below_floor')
                else:
                    self.assertEqual(result['state'], 'final')

    def test_k6_each_source_inventory_kind_delivers_three_material_allowances(self):
        kinds, records = reading_fixtures.read_inventory_kinds()
        self.assertEqual(kinds - set(reading_fixtures.DELIVERY_RULES), set())
        self.assertEqual(records - set(reading_fixtures.RECORD_DELIVERY_RULES), set())
        self.assertTrue({'carried_reading', 'target_page'} <= kinds)
        self.assertEqual(reading_fixtures.DELIVERY_RULES['list_page']['largest_size'],
                         max(capacity.describe_material_levels('reading')))
        self.assertEqual(reading_fixtures.DELIVERY_RULES['list_page']['largest_serialized_row'], v7.LIST_ROW_CHARACTERS)
        route = reading_fixtures.build_reading_route('codex_cli_v1', 'over_limit_expected')
        self.artifacts = reading_fixtures.MemoryArtifacts()
        initial = self.drive_inventory(route, reading_fixtures.build_shape_inventory(self.artifacts))
        allowance = initial['plan']['task']['reading']['material_allowance']
        for kind, record_kind in ([(kind, None) for kind in sorted(kinds - {'list_page', 'record'})]
                                 + [('record', name) for name in sorted(records)]):
            with self.subTest(kind=kind, record_kind=record_kind):
                self.artifacts = reading_fixtures.MemoryArtifacts()
                built = reading_fixtures.build_shape_inventory(self.artifacts, kind=kind,
                    record_kind=record_kind, characters=allowance * 3)
                result = self.drive_inventory(route, built)
                self.assertEqual(result['state'], 'final')
                self.assertGreater(result['turns'], 3)

    def test_k6_an_unexecuted_new_kind_requires_its_own_delivery_rule(self):
        kinds, records = reading_fixtures.read_inventory_kinds(dict(
            future='def build():\n    return dict(kind="new_unit", record_kind="new_record")\n'))
        self.assertEqual(kinds - set(reading_fixtures.DELIVERY_RULES), {'new_unit'})
        self.assertEqual(records - set(reading_fixtures.RECORD_DELIVERY_RULES), {'new_record'})

    def test_k6_list_pages_split_three_material_allowances_in_fixed_bounded_rows(self):
        route = reading_fixtures.build_reading_route('codex_cli_v1', 'over_limit_expected')
        allowance = 15000
        handles = ['t%d' % number for number in range(1000)]
        plan = dict(targets=dict(handles=handles))
        state = dict(allowances=dict(targets=[dict(handle=handle, characters=30000, used=0)
            for handle in handles]), list_pages={})
        self.assertGreater(capacity.measure_capacity_value(route, state['allowances']['targets']), 3 * allowance)
        after, seen = None, []
        for number in range(len(handles)):
            page = v7.build_list_page('allowances', after, plan, state, route, allowance)
            self.assertLessEqual(capacity.measure_capacity_value(route, page), allowance)
            self.assertTrue(page['rows'])
            for row in page['rows']:
                self.assertLessEqual(capacity.measure_capacity_value(route, row), 2000)
            seen.extend(row['id'] for row in page['rows'])
            state['list_pages']['allowances'] = dict(next=page['next'], ended=page['next'] is None)
            if page['next'] is None:
                break
            self.assertNotEqual(page['next'], after)
            after = page['next']
        else:
            self.fail('A finite list did not finish its bounded pages.')
        self.assertEqual(seen, handles)

    def test_two_thousand_gaps_remain_an_explicit_final_pages_gap(self):
        self.artifacts = reading_fixtures.MemoryArtifacts()
        native = reading_fixtures.build_native()
        native['scientific_context']['minimum_gaps'] = [dict(formulation=
            native['scientific_context']['openness_minimum']['statement'], text='Gap %d' % number)
            for number in range(2000)]
        result = self.drive_inventory(reading_fixtures.build_reading_route('codex_cli_v1', 'over_limit_expected'),
            reading_fixtures.build_shape_inventory(self.artifacts), native, shape_name='gaps_2000')
        self.assertEqual(result['state'], 'stop')
        row = capacity_guard.read_matrix()['reading_property_ends']['gaps_2000']
        self.assertEqual((row['entry'], row['closing_unit']), ('Q8', 'final-pages'))


class ReadingCapacityCommandTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('capacity-guard-2', capacity_guard.read_matrix()['installed_units'])

    def make_case(self, characters=50000):
        from openness_reading_fixtures import OpennessReadingCase
        from test_openness_reading_v7_turns import V7CommandTests
        case = OpennessReadingCase()
        case.text_sizes = (characters,)
        case.reading_protocol = None
        case.route_model = 'gpt-6-astra'
        case.route_output_tokens = 128000
        case.capacity_probe_tokens = 487900
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.build_v7_answer = V7CommandTests.build_v7_answer.__get__(case)
        case.build_v7_finding = V7CommandTests.build_v7_finding.__get__(case)
        return case

    def test_every_adapter_and_profile_class_executes_a_finding_or_declared_stop(self):
        from research_harness import review_protocol as protocol, review_codex_visual_transport as visual
        from capacity_evidence_fixtures import build_capacity_runtime
        from test_review_output_profile import build_row
        from test_openness_reading_v7_refusal import V7RefusalTests
        for adapter in capacity.CAPACITY_ADAPTERS:
            for category in ('fits', 'over_limit_expected', 'unlisted'):
                with self.subTest(adapter=adapter, category=category):
                    case = self.make_case()
                    if adapter == visual.ADAPTER:
                        runtime = patch.object(visual, 'runtime', return_value=build_capacity_runtime(adapter))
                        runtime.start()
                        case.addCleanup(runtime.stop)
                    route = reading_fixtures.build_reading_route(adapter, category)
                    route.pop('runtime', None)
                    route['id'] = 'property-route'
                    case.mutate(protocol.record_route, route)
                    def respond(selected, request, credential):
                        supplied = reading.review_excerpts.read_sent_packet(request)
                        return reading_fixtures.build_adapter_response(selected, request, dict(
                            allowed_control=supplied['allowed_control'], evidence_control=supplied['evidence_control'],
                            excluded_controls=[]))
                    if adapter != visual.ADAPTER:
                        with patch.object(protocol.transport, 'send_request', side_effect=respond):
                            case.mutate(protocol.probe_route, dict(id='property-probe', route_id=route['id']), credential='fixture')
                    def invoke(identifier, output, attempt=None):
                        with patch.object(protocol.transport, 'send_request', side_effect=lambda selected, request,
                                credential: reading_fixtures.build_adapter_response(selected, request, output)):
                            return case.mutate(protocol.invoke_assignment, dict(id=attempt or 'attempt-' + identifier,
                                assignment_id=identifier), credential='fixture')
                    case.invoke = invoke
                    def assign(identifier, native, prior=None):
                        context = dict(native_packet=native)
                        if prior is not None:
                            context['prior_assignment_id'] = prior
                        return case.mutate(protocol.record_assignment, dict(id=identifier, route_id=route['id'],
                            dossier_id=None, role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                            context=context))['result']
                    case.assign = assign
                    over = int(category == 'over_limit_expected')
                    priors = [] if category == 'unlisted' else [build_row(level=None, thinking=1000, count=8, over=over),
                        build_row(kind='final', level=None, thinking=1000, count=8, over=over)]
                    with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), patch.object(
                            capacity, 'read_profile_priors', return_value=priors):
                        native = case.export()
                        try:
                            receipt = case.assign('turn-1', native)
                        except ResearchError as error:
                            if adapter == visual.ADAPTER:
                                self.assertEqual(error.code, v7.VISUAL_TEXT_ROUTE_CODE)
                                self.assertNotIn('turn-1', case.store.snapshot()['records'].get('review_assignment', {}))
                                reading_fixtures.execute_visual_text_answer(self, route, error.details, case=case)
                                print('READING_COMMAND_END ' + json.dumps(dict(adapter=adapter, category=category,
                                    end=error.code)), flush=True)
                                continue
                            self.assertEqual(error.code, 'review_route_capacity_below_floor')
                            self.assertNotIn('turn-1', case.store.snapshot()['records'].get('review_assignment', {}))
                            print('READING_COMMAND_END ' + json.dumps(dict(adapter=adapter, category=category,
                                end=error.code)), flush=True)
                            continue
                        if receipt['protocol_selection']['protocol'] == reading.PROTOCOL_V6:
                            V7RefusalTests.finish_from(case, native, 'turn-1')
                            print('READING_COMMAND_END ' + json.dumps(dict(adapter=adapter, category=category,
                                end='recorded_finding_v6')), flush=True)
                        else:
                            if category != 'unlisted':
                                plan = capacity.read_chain_capacity_plan(case.artifacts, receipt)
                                self.assertEqual(plan['task']['reading']['state'], category)
                            self.assertEqual(self.drive_commands(case, native)['end'], 'recorded_finding')
                            print('READING_COMMAND_END ' + json.dumps(dict(adapter=adapter, category=category,
                                end='recorded_finding_v7')), flush=True)

    def drive_commands(self, case, native, head='turn-1', stub='plain'):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        observed, replacements = [], []
        for number in range(1, 2000):
            records = case.store.snapshot()['records']
            assignment = records['review_assignment'][head]
            packet = case.read_packet(head)
            kind = 'reading' if 'reading_turn' in packet else 'final'
            binding = packet['reading_turn' if kind == 'reading' else 'reading_final']
            route = records['review_route'][assignment['route_id']]
            plan = capacity.read_chain_capacity_plan(case.artifacts, assignment)
            self.assertEqual(validate_packet('native_math', packet, registry,
                request_limit_characters=plan['limits']['input_text_characters']['value']), [])
            request = protocol.build_assignment_request(records, case.artifacts, assignment)
            comparison = capacity.describe_plan_preflight(records, route, request, plan, kind)
            self.assertTrue(comparison['plan_comparison']['fits'], comparison)
            action = plan_next(records, case.artifacts, head)
            self.assertEqual(action['state'], 'run', action)
            if kind == 'final' and stub == 'fill_final':
                return dict(end='pending_final', head=head)
            overflow = stub == 'every_level' or stub == 'once' and not replacements
            if overflow:
                def respond(selected, sent, credential):
                    response = build_codex_response(selected, sent, {})
                    response['status'] = 'incomplete'
                    response['usage']['output_tokens'] = binding['capacity']['output_tokens']
                    return bind_codex_response(selected, sent, response)
                with patch.object(protocol.transport, 'send_request', side_effect=respond):
                    case.mutate(protocol.invoke_assignment, dict(id='over-%d' % number, assignment_id=head), credential='fixture')
                action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                if action['state'] == 'stop':
                    self.assertEqual(stub, 'every_level')
                    self.assertIn('decision', action['details'])
                    self.assertEqual(binding['capacity']['material_allowance'],
                        min(row['material_allowance'] for row in plan['task'][kind]['levels']),
                        dict(code=action.get('code'), stopped_level=binding['capacity']['material_allowance'],
                             next_level=action['details'].get('material_allowance'), replacements=replacements))
                    return dict(end='decision_after_floor', replacements=replacements)
                self.assertEqual((action['state'], action['command']), ('replace', 'review-assignment'), action)
                replacement = case.mutate(protocol.record_assignment, action['payload'])['result']
                new_packet = case.read_packet(replacement['id'])
                new_binding = new_packet.get('reading_turn', new_packet.get('reading_final'))
                levels = [row['material_allowance'] for row in plan['task'][kind]['levels']]
                previous = binding['capacity']['material_allowance']
                self.assertEqual(new_binding['capacity']['material_allowance'], max(value for value in levels if value < previous))
                self.assertEqual(replacement['route_id'], assignment['route_id'])
                replacements.append((previous, new_binding['capacity']['material_allowance']))
                head = replacement['id']
                continue
            if kind == 'final':
                output = case.build_v7_finding(packet, binding['notes_handle'])
                if stub == 'twice_bound':
                    output['findings']['statement'] = 's' * (2 * binding['capacity']['answer_bound'])
            else:
                output = case.build_v7_answer(packet)
                output['notes'] = 'n' * binding['allowances']['notes']
                width = 1 if stub == 'shortest' else 6000
                room = binding['allowances']['excerpts']
                for target in binding['allowances']['targets']:
                    target_room = target['remaining']
                    for row, body in zip(binding['plan']['now'], packet['evidence']):
                        if row['kind'] == 'list_page':
                            continue
                        def measure_excerpt(length):
                            text = body['content'][:length]
                            return capacity.measure_capacity_value(route, dict(kind='excerpt', handle='e999999999',
                                unit=row['handle'], text=text, purpose='p' * 200, targets=[target['handle']],
                                start=row['start'], end=row['start'] + len(text), sha256='x' * 64, status='verified')) + capacity.measure_capacity_value(
                                    route, {key: row[key] for key in ('handle', 'kind', 'label')})
                        while measure_excerpt(1) <= min(room, target_room):
                            low, high = 1, min(width, len(body['content']))
                            while low < high:
                                middle = (low + high + 1) // 2
                                if measure_excerpt(middle) <= min(room, target_room):
                                    low = middle
                                else:
                                    high = middle - 1
                            text = body['content'][:low]
                            cost = measure_excerpt(low)
                            output['excerpts'].append(dict(unit=row['handle'], text=text, purpose='p' * 200, targets=[target['handle']]))
                            room -= cost
                            target_room -= cost
                if stub == 'twice_bound':
                    row, body = binding['plan']['now'][0], packet['evidence'][0]
                    while len(json.dumps(output)) <= 2 * binding['capacity']['answer_bound']:
                        output['excerpts'].append(dict(unit=row['handle'], text=body['content'][:1000],
                            purpose='p' * 200, targets=['t1']))
            case.invoke(head, output)
            records = case.store.snapshot()['records']
            attempt = records['review_attempt']['attempt-' + head]
            self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
            if stub == 'twice_bound':
                self.assertGreater(attempt['answer']['characters'], 2 * attempt['answer']['bound'])
            observed.append(head)
            action = plan_next(records, case.artifacts, head)
            if kind == 'final':
                self.assertEqual(action['state'], 'record', action)
                result = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=head))['result']
                self.assertEqual(result['outcome'], 'unresolved')
                self.assertEqual(reading.verify_chain(case.store.snapshot()['records'], case.artifacts, assignment)[-1]['assignment_id'], head)
                return dict(end='recorded_finding', turns=observed, replacements=replacements)
            self.assertEqual((action['state'], action['command']), ('continue', 'review-assignment'), action)
            head = case.mutate(protocol.record_assignment, action['payload'])['result']['id']
        self.fail('The finite command chain did not reach a finding or a declared stop.')

    def test_v7_allowance_and_over_bound_stubs_execute_planner_to_recorded_finding(self):
        for stub in ('shortest', 'longest', 'twice_bound', 'once'):
            with self.subTest(stub=stub):
                case = self.make_case()
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                    native = case.export()
                    case.assign('turn-1', native)
                    result = self.drive_commands(case, native, stub=stub)
                self.assertEqual(result['end'], 'recorded_finding')

    def test_v7_output_cap_at_every_level_reaches_the_floor_before_decision(self):
        case = self.make_case()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = case.export()
            case.assign('turn-1', native)
            self.assertEqual(self.drive_commands(case, native, stub='every_level')['end'], 'decision_after_floor')

    def make_dense_reading_case(self):
        import math
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness import review_protocol as protocol
        from test_review_output_profile import build_row
        case = self.make_case(characters=200000)
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        for active in (patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)),
                       patch.object(capacity, 'read_profile_priors', return_value=priors)):
            active.start()
            case.addCleanup(active.stop)
        native = case.export()
        first = case.assign('turn-1', native)
        plan = capacity.read_chain_capacity_plan(case.artifacts, first)
        output = case.build_v7_answer(case.read_packet('turn-1'))
        def respond(route, request, credential):
            response = build_codex_response(route, request, output)
            characters = capacity.measure_request(route, request)['text_characters']
            response['usage']['input_tokens'] = math.ceil(characters / plan['measure']['characters_per_token'] * 1.2)
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            case.mutate(protocol.invoke_assignment, dict(id='dense-first', assignment_id='turn-1'), credential='fixture')
        old = copy.deepcopy(case.store.snapshot()['records']['review_attempt']['dense-first'])
        return case, native, plan, old

    def test_v7_twenty_percent_denser_measurement_keeps_fitting_turns_and_records(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        case, native, plan, old = self.make_dense_reading_case()
        action = plan_next(case.store.snapshot()['records'], case.artifacts, 'turn-1')
        self.assertEqual(action['state'], 'continue', action)
        following = case.mutate(protocol.record_assignment, action['payload'])['result']
        result = self.drive_commands(case, native, head=following['id'])
        self.assertEqual(result['end'], 'recorded_finding')
        final = case.read_packet(result['turns'][-1])['reading_final']
        self.assertLessEqual(final['capacity']['material_allowance'], plan['task']['final']['material_allowance'])
        self.assertLess(capacity.measure_attempt_ratio(old, margin=plan['measure']['ratio_margin']),
                        plan['measure']['characters_per_token'] / 1.2)
        self.assertEqual(case.store.snapshot()['records']['review_attempt']['dense-first'], old)

    def test_v7_new_search_and_twenty_percent_denser_measurement_export_then_record(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from research_harness.check_mode import check_mutation
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        case, native, plan, old = self.make_dense_reading_case()
        case.searches.append(ProblemOpennessMinimumTests.search(case, case.versions,
            query='A distinct search after the measured first turn.'))
        from research_harness.problem_openness import assess_packet_freshness
        changed = assess_packet_freshness(case.store.snapshot()['records'], case.artifacts,
                                         json.loads(case.artifacts.read(native)))
        self.assertFalse(changed['current'], changed)
        action = plan_next(case.store.snapshot()['records'], case.artifacts, 'turn-1')
        self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
        successor = case.export(name='changed-export', assignment_id='successor')
        payload = dict(id='successor', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=successor))
        checked = check_mutation(case.store, protocol.record_assignment, payload,
            expected_revision=case.store.revision, request_id='changed-check', bounded_payload=payload)
        assigned = case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(checked['protocol_selection'], assigned['protocol_selection'])
        stored = capacity.read_chain_capacity_plan(case.artifacts, assigned)
        self.assertLessEqual(stored['measure']['characters_per_token'], plan['measure']['characters_per_token'] / 1.2)
        self.assertEqual(stored['measure']['source'], dict(chain='turn-1', attempt='dense-first'))
        self.assertTrue(assigned['carried_readings']['chains'])
        self.assertEqual(self.drive_commands(case, successor, head='successor')['end'], 'recorded_finding')
        self.assertEqual(case.store.snapshot()['records']['review_attempt']['dense-first'], old)

    def test_ruling39_a_refused_full_final_keeps_its_level_material_and_records(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from test_review_output_profile import build_row
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        from research_harness.problem_openness import export_openness
        case = self.make_case()
        case.selected = case.select_statement(identifier='four-targets', candidate_id='candidate-1',
            neighbours=['Nearby one.', 'Nearby two.', 'Nearby three.'])
        searches = [case.search] + [ProblemOpennessMinimumTests.search(case, formulation=formulation)
            for formulation in case.selected['neighbours']]
        priors = [build_row(kind='final', level=240000, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8, over=1)]
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), patch.object(
                capacity, 'read_profile_priors', return_value=priors):
            native = export_openness(case.store, dict(assignment_id='turn-1', reviewer_id='openness-reviewer',
                minimum=dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in searches],
                    settling_work_id=None, duplicate_resolutions=[])), case.root / 'ruling39-export')['native_packet']
            case.assign('turn-1', native)
            head = self.drive_commands(case, native, stub='fill_final')['head']
            before = case.read_packet(head)
            self.assertEqual(before['reading_final']['capacity']['material_allowance'], 120000)
            case.invoke(head, dict(schema_version=3, decision='revise', reason='x' * 100000))
            frozen_records = copy.deepcopy(case.store.snapshot()['records'])
            action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
            self.assertEqual((action['state'], action.get('command')), ('replace', 'review-assignment'), action)
            replacement = case.mutate(protocol.record_assignment, action['payload'])['result']
            after = case.read_packet(replacement['id'])
            self.assertIn('output_refusal', after)
            for field in v7.FINAL_MATERIAL_FIELDS:
                self.assertEqual(digest(after['reading_final'][field]), digest(before['reading_final'][field]), field)
            self.assertEqual(after['reading_final']['capacity']['material_allowance'],
                             before['reading_final']['capacity']['material_allowance'])
            self.assertEqual(self.drive_commands(case, native, head=replacement['id'])['end'], 'recorded_finding')
            recorded = case.store.snapshot()['records']
            for group in ('review_assignment', 'review_attempt', 'review_output_refusal'):
                for key, row in frozen_records.get(group, {}).items():
                    self.assertEqual(digest(recorded[group][key]), digest(row))

    def test_k4_pending_material_and_completed_argument_packets_record_a_finding(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        case = self.make_case()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = case.export()
            case.assign('turn-1', native)
            head = self.drive_commands(case, native, stub='fill_final')['head']
            for part in (1, 2):
                case.invoke(head, dict(stage='material', title='A finite argument', part=part,
                    text='A checked step.', more=part == 1))
                action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                self.assertEqual(action['state'], 'continue', action)
                head = case.mutate(protocol.record_assignment, action['payload'])['result']['id']
                packet = case.read_packet(head)
                self.assertEqual(validate_packet('native_math', packet, read_packet_registry()), [])
                if part == 1:
                    self.assertEqual(packet['reading_final']['material_request']['part'], 2)
            self.assertEqual(self.drive_commands(case, native, head=head)['end'], 'recorded_finding')

    def test_v7_thirty_failed_attempts_with_sixty_character_ids_then_records(self):
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response, bind_codex_response
        case = self.make_case()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = case.export()
            case.assign('turn-1', native)
            def respond(route, request, credential):
                response = build_codex_response(route, request, {})
                response['status'] = 'incomplete'
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                for number in range(30):
                    case.mutate(protocol.invoke_assignment, dict(id='a' + str(number).zfill(59),
                        assignment_id='turn-1'), credential='fixture')
            attempts = [row for row in case.store.snapshot()['records']['review_attempt'].values()]
            self.assertEqual(len({row['request']['sha256'] for row in attempts}), 1)
            from research_harness.review_continuation import plan_next
            stop = plan_next(case.store.snapshot()['records'], case.artifacts, 'turn-1')
            self.assertEqual(stop['code'], 'review_turn_failures_exhausted')
            self.assertEqual(stop['details']['decision']['then'][1]['steps'][0]['command'], 'review-context')
            evidence = case.artifacts.put(b'Synthetic owner authorization to continue this fixture.', 'text/plain')
            from research_harness import review_failure_class
            for number in range(30):
                records = case.store.snapshot()['records']
                position = review_failure_class.count_position(records, case.artifacts,
                    records['review_assignment']['turn-1'])
                if position['permitted_sequences'] > position['sequence']:
                    break
                case.mutate(protocol.record_context_event, dict(id='grant-after-thirty-%d' % number,
                    assignment_id='turn-1', source='owner', kind='observation',
                    reason='Continue this synthetic review.', evidence=[evidence]))
            from datetime import datetime, timedelta, timezone
            class AfterCooldown(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(timezone.utc) + timedelta(days=1)
            with patch.object(review_failure_class, 'datetime', AfterCooldown):
                self.assertEqual(self.drive_commands(case, native)['end'], 'recorded_finding')

    def test_final_pages_five_and_twenty_thousand_targets_stop_both_admissions_then_execute_smaller_answer(self):
        from research_harness import review_protocol as protocol
        from research_harness.problem_openness import export_openness
        for count in (5000, 20000):
            with self.subTest(targets=count):
                case = self.make_case(characters=200)
                case.selected = case.select_statement(identifier='many-targets', candidate_id='candidate-1',
                    neighbours=['Neighbour %d.' % number for number in range(count - 1)])
                # Seed repeated canonical search rows in one fixture transaction;
                # commands still export, validate and refuse the complete minimum.
                formulations = case.selected['neighbours']
                def seed_searches(tx):
                    for number, formulation in enumerate(formulations, 1):
                        row = dict(copy.deepcopy(case.search), id='shape-search-%d' % number,
                                   question=formulation, queries=[formulation])
                        tx.put('literature_search', row['id'], row)
                    return {}
                case.store.mutate('fixture.search-shape', dict(targets=count), seed_searches,
                    expected_revision=case.store.revision, request_id='seed-shape')
                minimum = dict(searches=[dict(formulation=case.selected['statement'], search_id=case.search['id'])] +
                    [dict(formulation=formulation, search_id='shape-search-%d' % number)
                    for number, formulation in enumerate(formulations, 1)],
                               settling_work_id=None, duplicate_resolutions=[])
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                    native = export_openness(case.store, dict(assignment_id='turn-1', reviewer_id='openness-reviewer',
                        minimum=minimum), case.root / 'many-export', accept_gaps=True)['native_packet']
                    revision = case.store.revision
                    with patch.object(protocol.transport, 'send_request') as send, self.assertRaises(ResearchError) as caught:
                        case.assign('turn-1', native)
                    send.assert_not_called()
                    self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
                    details = caught.exception.details
                    self.assertEqual({row['protocol'] for row in details['admissions']}, {v7.PROTOCOL, reading.PROTOCOL_V6})
                    self.assertTrue(all(row['state'] == 'stop' for row in details['admissions']))
                    self.assertEqual(case.store.revision, revision)
                    self.assertEqual(len(details['decision']['then']), 2)
                    self.assertFalse(details['serving_routes'])
                    self.assertTrue(details['unserved_range'])
                    # The route answer explicitly names the unserved range:
                    # no registered serving route is hidden behind an export.
                    route_answer = details['decision']['then'][1]
                    self.assertEqual(route_answer['steps'][0]['command'], 'review-route')
                    # Execute the smaller-packet answer's prerequisite, export,
                    # public check, assignment and chain through recording.
                    case.selected = case.select_statement(identifier='smaller', candidate_id='candidate-1', neighbours=[])
                    smaller = case.export(name='smaller-export')
                    from research_harness.check_mode import check_mutation
                    payload = dict(id='turn-1', route_id='route', dossier_id=None, role='native_math',
                        reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=smaller))
                    checked = check_mutation(case.store, protocol.record_assignment, payload,
                        expected_revision=case.store.revision, request_id='smaller-check', bounded_payload=payload)
                    assigned = case.mutate(protocol.record_assignment, payload)['result']
                    self.assertEqual(checked['protocol_selection'], assigned['protocol_selection'])
                    self.assertEqual(self.drive_commands(case, smaller)['end'], 'recorded_finding')
