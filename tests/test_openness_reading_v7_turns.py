"""Bounded v7 turns. Synthetic shape of the 57-turn final-over-limit incident."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_route_capacity as capacity
from research_harness.artifacts import describe_artifact
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from v7_capacity_fixtures import build_route


class MemoryArtifacts:
    def __init__(self):
        self.bodies = {}

    def put(self, content, media_type='application/json'):
        reference = describe_artifact(content, media_type)
        self.bodies[reference['sha256']] = content
        return reference

    def read(self, reference):
        return self.bodies[reference['sha256']]

    def put_json(self, value):
        from research_harness.problem_openness import serialize_record
        return self.put(serialize_record(value))


def build_native(targets=1):
    return dict(subject_digest='a' * 64, claim_digest='b' * 64, reviewer='fixture-reviewer',
        subject=dict(statement='A synthetic exact statement.'),
        scientific_context=dict(openness_minimum=dict(schema_version=2, statement='A synthetic exact statement.',
            neighbours=['Neighbour %d. ' % index + 'x' * 60 for index in range(targets - 1)], searches=[],
            reading_digests=[], results=[]), minimum_gaps=[],
            audit_forecast=dict(outcomes=dict(supported_open='blocked', already_solved='blocked'),
                                searched_through='2026-11-13'), decisive_work_ids=[]))


def build_inventory(artifacts, characters=40000, unicode=False):
    content = (('abc日本' if unicode else 'abcde') * (characters // 5)).encode('utf-8')
    reference = artifacts.put(content, 'text/plain; charset=utf-8')
    row = dict(kind='fulltext', version_id='fixture-work', text_sha256=reference['sha256'], artifact=reference)
    gaps = artifacts.put_json([])
    return v7.plan_inventory([row, dict(kind='record', record_kind='minimum_gaps',
        record_id='minimum_gaps', artifact=gaps)], {reference['sha256']: content, gaps['sha256']: b'[]'})


class RatioAndAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = MemoryArtifacts()
        self.route = dict(build_route(effort='max'), id='route')
        self.native = build_native()
        self.records = dict(review_route={'route': self.route}, review_assignment={}, review_attempt={})

    def add_chain(self, name, ratio=None, measured=None, reviewer='another-reviewer'):
        assignment = dict(id=name, role='native_math', reviewer_id=reviewer, route_id='route',
                          context=dict(native_packet=self.artifacts.put_json(self.native)),
                          reading_chain=dict(first_assignment_id=name, turn=1))
        if ratio is not None:
            plan = dict(measure=dict(characters_per_token=ratio, ratio_margin=capacity.TOKEN_RATIO_MARGIN))
            assignment['capacity_plan'] = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL,
                digest=digest(plan), artifact=self.artifacts.put_json(plan))
        self.records['review_assignment'][name] = assignment
        if measured is not None:
            self.records['review_attempt'][name + '-failed-final'] = dict(id=name + '-failed-final',
                assignment_id=name, status='failed', usage=dict(sends=1, input_tokens=100000,
                request_characters=int(measured * 100000)), recorded_revision=2)
        return assignment

    def test_failed_final_of_another_reviewer_inherits_margin_once_without_carried_turns(self):
        self.add_chain('old', ratio=1.36, measured=1.0)
        value = v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route)
        self.assertEqual(value['characters_per_token'], 0.95)
        self.assertEqual(value['source'], dict(chain='old', attempt='old-failed-final'))
        self.add_chain('successor', ratio=value['characters_per_token'])
        self.assertEqual(v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route), value)
        self.records['review_attempt']['old-failed-final']['usage']['sends'] = 2
        value = v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route)
        self.assertEqual(value['characters_per_token'], 0.95)
        self.assertEqual(value['source'], dict(chain='successor', plan=self.records['review_assignment']['successor']['capacity_plan']['digest']))

    def test_ratio_identity_includes_statement_claim_and_route_configuration(self):
        old = self.add_chain('old', ratio=0.2, measured=0.1)
        self.records['review_route']['other'] = dict(self.route, configuration=dict(self.route['configuration'], effort='xhigh'))
        old['route_id'] = 'other'
        self.assertIsNone(v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route))
        old['route_id'] = 'route'
        different = dict(self.native, claim_digest='c' * 64)
        self.assertIsNone(v7.derive_inherited_ratio(self.records, self.artifacts, different, self.route))

    def test_q7_final_floor_admits_eight_targets_and_refuses_nine_with_numbers(self):
        inventory = v7.plan_inventory([], {})
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        with patch.object(v7, 'build_inventory', return_value=inventory):
            accepted = v7.check_v7_admission(self.records, self.artifacts, route, build_native(8))
            refused = v7.check_v7_admission(self.records, self.artifacts, route, build_native(9))
        self.assertEqual(accepted['state'], 'record')
        self.assertEqual(accepted['capacity_plan']['task']['final']['material_allowance'], 60000)
        self.assertEqual(refused['reason'], 'final_needs_pages')
        self.assertEqual(refused['pages'], 2)
        self.assertEqual(refused['targets'], 9)
        self.assertEqual(refused['largest_one_request_targets'], 8)

    def test_reduced_frame_moves_statement_and_labels_before_admission(self):
        artifacts = self.artifacts
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        native = build_native()
        native['subject']['statement'] = 's' * 90000
        native['scientific_context']['openness_minimum']['statement'] = 's' * 90000
        built = build_inventory(artifacts)
        with patch.object(v7, 'build_inventory', return_value=built), patch.object(
                capacity, 'read_profile_priors', return_value=[]), patch.object(v7, 'derive_inherited_ratio',
                return_value=dict(characters_per_token=.6, source=dict(chain='old', attempt='measured'))):
            admission = v7.check_v7_admission({}, artifacts, route, native)
        self.assertEqual(admission['state'], 'record')
        self.assertTrue(any(row['kind'] in ('fixed_field', 'target_page') for row in admission['inventory']['inventory']['units']))
        logical = dict(protocol='fixture', role='native_math', dossier_id=None, reviewer_id=native['reviewer'], native=native)
        packet, chain = v7.prepare_first_assignment({}, artifacts, dict(id='chain'), route, logical, [], admission=admission)
        from research_harness.review_protocol import canonical_prompt
        from research_harness.review_transport import request_body
        request = request_body(route, canonical_prompt('native_math', reading_stage='reading', reading_protocol=v7.PROTOCOL)['text'], packet)
        self.assertTrue(capacity.describe_plan_preflight({}, route, request, admission['capacity_plan'], 'reading')['plan_comparison']['fits'])

    def test_unread_core_refuses_when_the_final_frame_exceeds_the_measured_text_room(self):
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        native = build_native()
        native['subject']['statement'] = 's' * 90000
        native['scientific_context']['openness_minimum']['statement'] = 's' * 90000
        # Keep the reading notes reserve at its minimum so this boundary isolates the final frame.
        built = build_inventory(self.artifacts)
        with patch.object(v7, 'build_inventory', return_value=built), patch.object(
                capacity, 'read_profile_priors', return_value=[]), patch.object(capacity, 'NOTES_MAXIMUM_CHARACTERS', 6000), patch.object(v7, 'derive_inherited_ratio',
                return_value=dict(characters_per_token=.54, source=dict(chain='old', attempt='measured'))):
            with patch.object(v7, 'UNREAD_CORE_CHARACTERS', 0):
                before = v7.check_v7_admission({}, self.artifacts, route, native)
            after = v7.check_v7_admission({}, self.artifacts, route, native)
        self.assertEqual(before['state'], 'record')
        self.assertEqual(before['capacity_plan']['task']['final']['request_bound']['text_characters'], 125079)
        self.assertEqual((after['state'], after['code']), ('stop', 'review_route_capacity_below_floor'))
        self.assertEqual(after['details']['reason'], 'fixed_frame')
        self.assertEqual(after['details']['room'], 125226)
        self.assertEqual(after['details']['frames']['final']['characters'] + 45000 + 60000, 126579)

    def test_same_measurement_does_not_compound_margin_across_three_successors(self):
        self.add_chain('first', ratio=1.36, measured=1.0)
        for number in range(3):
            inherited = v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route)
            self.assertEqual(inherited['characters_per_token'], .95)
            self.add_chain('successor-%d' % number, ratio=inherited['characters_per_token'])
        self.add_chain('new-measurement', measured=.8)
        self.assertEqual(v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route)['characters_per_token'], .76)

    def test_first_assignment_publishes_the_exact_admitted_plan_reference(self):
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        built = build_inventory(self.artifacts)
        with patch.object(v7, 'build_inventory', return_value=built):
            admitted = v7.check_v7_admission(self.records, self.artifacts, route, self.native)
        logical = dict(protocol='fixture', role='native_math', reviewer_id=self.native['reviewer'], dossier_id=None, native=self.native)
        _, chain = v7.prepare_first_assignment(self.records, self.artifacts, dict(id='chain'), route, logical, [], admission=admitted)
        self.assertEqual(chain['capacity_plan'], admitted['capacity_members']['capacity_plan'])
        self.assertEqual(json.loads(self.artifacts.read(chain['capacity_plan']['artifact'])), admitted['capacity_plan'])
        self.add_chain('last', ratio=.76)
        self.assertEqual(v7.derive_inherited_ratio(self.records, self.artifacts, self.native, self.route)['characters_per_token'], .76)


class ListPageTests(unittest.TestCase):
    def setUp(self):
        self.route = build_route(effort='xhigh')
        self.plan = dict(targets=dict(handles=['t%d' % n for n in range(180)]))
        self.state = dict(allowances=dict(targets=[dict(handle=h, characters=30000, used=0)
                                                for h in self.plan['targets']['handles']]), list_pages={})

    def test_q8_pages_keep_plan_order_when_used_order_changes(self):
        after, seen = None, []
        while True:
            page = v7.build_list_page('allowances', after, self.plan, self.state, self.route, 5000)
            seen += [row['id'] for row in page['rows']]
            self.state['list_pages']['allowances'] = dict(next=page['next'], ended=page['next'] is None)
            self.state['allowances']['targets'][0]['used'] += 1
            self.state['allowances']['targets'][-1]['used'] += 4
            if page['next'] is None:
                break
            after = page['next']
        self.assertEqual(seen, self.plan['targets']['handles'])
        self.assertEqual(len(seen), len(set(seen)))
        with self.assertRaises(ResearchError):
            v7.build_list_page('allowances', after, self.plan, self.state, self.route, 5000)

    def test_invalid_cursors_and_oversized_supplier_rows(self):
        self.state['list_pages']['allowances'] = dict(next='t12', ended=False)
        for name, after in [('allowances', None), ('allowances', 't4'), ('allowances', 'missing'), ('missing', None)]:
            with self.subTest(name=name, after=after), self.assertRaises(ResearchError):
                v7.build_list_page(name, after, self.plan, self.state, self.route, 5000)
        supplier = dict(supply=lambda plan, state, artifacts: [dict(id='x', text='x' * 2100)],
                        row_characters=2000, fixed_at='first_assignment')
        with patch.dict(v7.LIST_PAGE_SUPPLIERS, fixture=supplier), self.assertRaises(ResearchError) as error:
            v7.build_list_page('fixture', None, self.plan, self.state, self.route, 5000)
        self.assertEqual(error.exception.code, 'review_route_capacity_plan_violated')

    def test_supplier_requires_bounded_rows_and_an_explicit_order_binding_time(self):
        base = dict(supply=lambda plan, state, artifacts: [dict(id='x')],
                    row_characters=512, fixed_at='first_assignment')
        for key, value in [('row_characters', None), ('row_characters', 2001), ('fixed_at', None)]:
            supplier = dict(base)
            if value is None:
                del supplier[key]
            else:
                supplier[key] = value
            with self.subTest(key=key, value=value), patch.dict(v7.LIST_PAGE_SUPPLIERS, fixture=supplier):
                with self.assertRaises(ResearchError) as error:
                    v7.build_list_page('fixture', None, self.plan, self.state, self.route, 5000)
                self.assertEqual(error.exception.code, 'review_route_capacity_plan_violated')


class HandleWireTests(unittest.TestCase):
    def test_malformed_final_is_captured_before_shape_refusal_and_native_refs_are_refused(self):
        from research_harness.review_handles import describe_expansion
        packet = dict(reading_final=dict(protocol=v7.PROTOCOL), handle_table={})
        for output in ([], dict(findings=[]), dict(findings=dict(openness=dict(grounds=[None])))):
            response = dict(output=[dict(type='message', content=[dict(type='output_text', text=json.dumps(output))])])
            expanded, certificate = describe_expansion(packet, output, response)
            self.assertEqual(expanded, output)
            self.assertEqual(certificate['references'], 0)
        output = dict(findings=dict(openness=dict(grounds=[dict(refs=[dict(sha256='x' * 64,
            path='research/sources/objects/' + 'x' * 64, size=1, media_type='text/plain')])])))
        with self.assertRaises(ResearchError) as error:
            describe_expansion(packet, output, {})
        self.assertEqual(error.exception.code, 'invalid_reference_handle')

    def test_material_title_has_v7_field_bound(self):
        from research_harness.review_handles import validate_material
        packet = dict(reading_final=dict(protocol=v7.PROTOCOL), handle_table={}, capacity=dict(answer_bound=18000))
        with self.assertRaises(ResearchError) as error:
            validate_material(packet, dict(stage='material', title='T' * 121, part=1, text='x', more=True))
        self.assertEqual(error.exception.code, 'invalid_review_material')


class TurnPacketTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = MemoryArtifacts()
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        self.native = build_native(3)
        self.built = build_inventory(self.artifacts, 100000)
        self.value = dict(id='chain', role='native_math', reviewer_id='fixture-reviewer',
                          dossier_id=None, route_id='route', author_id='author', context={})
        self.packet = dict(protocol='fixture-native', role='native_math', reviewer_id='fixture-reviewer',
                           dossier_id=None, native=self.native, evidence=[], digest='unused')

    def prepare(self):
        with patch.object(v7, 'build_inventory', return_value=self.built):
            return v7.prepare_first_assignment({}, self.artifacts, self.value, self.route, self.packet, [])

    def answer(self, packet, text='A retained note.'):
        binding = packet['reading_turn']
        return dict(schema_version=2, stage='reading', notes=text, excerpts=[], gaps=[], passage_requests=[],
                    **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})

    def test_q1_turn_one_and_sixty_have_bounded_reading_requests(self):
        packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state = json.loads(self.artifacts.read(chain['state']))
        limits = capacity.derive_plan_task_limits(plan, 'reading')
        sizes = []
        for number in (1, 60):
            current = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', number,
                inventory, state, plan, limits, 'reading')
            from research_harness.review_protocol import canonical_prompt
            from research_harness.review_transport import request_body
            request = request_body(self.route, canonical_prompt('native_math', reading_stage='reading',
                reading_protocol=v7.PROTOCOL)['text'], current)
            report = capacity.describe_plan_preflight({}, self.route, request, plan, 'reading')
            self.assertTrue(report['plan_comparison']['fits'], report['plan_comparison'])
            sizes.append(report['request_measure']['text_characters'])
            if number == 1:
                without_index = copy.deepcopy(current['reading_turn'])
                without_index['affected_notes'].pop('index')
                index_cost = capacity.measure_capacity_value(self.route, current['reading_turn']) - capacity.measure_capacity_value(
                    self.route, without_index)
            self.assertLessEqual(capacity.measure_capacity_value(self.route, current['evidence']) +
                capacity.measure_capacity_value(self.route, current['reading_turn']['plan']['now']),
                limits['material_allowance'])
            self.assertNotIn('handle_table', current)
            for name in ('turns', 'receipts', 'inventory', 'attempt_id', 'run'):
                self.assertNotIn(name, current['reading_turn'])
        self.assertLessEqual(abs(sizes[1] - sizes[0] + index_cost), 1)

    def test_exact_preflight_refuses_a_request_above_the_pinned_kind_bound(self):
        from research_harness import review_preflight, review_protocol
        from research_harness.review_transport import request_body
        packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        prompt = review_protocol.canonical_prompt('native_math', reading_stage='reading', reading_protocol=v7.PROTOCOL)
        request = request_body(self.route, prompt['text'] + 'x' * plan['task']['reading']['request_bound']['text_characters'], packet)
        report = capacity.describe_plan_preflight({}, self.route, request, plan, 'reading')
        with self.assertRaises(ResearchError) as error:
            review_preflight.check_request(report, request)
        self.assertEqual(error.exception.code, 'review_route_capacity_plan_violated')
        sizes = error.exception.details['sizes']['text_characters']
        self.assertGreater(sizes['value'], sizes['bound'])

    def test_over_allowance_entries_are_recorded_and_state_rebuilds(self):
        packet, chain = self.prepare()
        row = packet['reading_turn']['plan']['now'][0]
        answer = self.answer(packet)
        answer['excerpts'] = [dict(unit=row['handle'], text=packet['evidence'][0]['content'][:1000],
            purpose='A relevant range.', targets=['t1']) for _ in range(80)]
        v7.validate_turn_output(packet, answer)
        projection = v7.project_own_turn(self.artifacts, self.route, packet, answer, 'chain', 'attempt')
        self.assertTrue(any(row['status'] == 'over_allowance' for row in projection['entries']))
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        rebuilt = v7.build_chain_state(inventory, [projection], route=self.route, plan=plan)
        self.assertIsNone(rebuilt['state']['turns'][0]['origin'])
        self.assertEqual(rebuilt['artifact']['sha256'], digest(rebuilt['state']))
        self.assertLessEqual(rebuilt['state']['allowances']['targets'][0]['used'],
                             rebuilt['state']['allowances']['targets'][0]['characters'])

    def test_labels_and_allowance_views_are_bounded_for_twenty_thousand_targets(self):
        for count in (5000, 20000):
            native = build_native(count)
            built = v7.prepare_inventory(self.built, native, self.route)
            self.assertTrue(any(row['kind'] == 'target_page' for row in built['inventory']['units']))
            state = dict(allowances=dict(notes=6000, targets=[dict(handle='t%d' % n,
                characters=30000, used=n % 20000) for n in range(1, count + 1)],
                passages=dict(characters=100000, used=0)))
            view = v7.describe_allowances(self.route, state, 18000)
            self.assertLessEqual(capacity.measure_capacity_value(self.route, view), 30000)
            self.assertEqual(view['target_count'], count)
            self.assertGreater(view['omitted'], 0)

    def test_compact_allowance_default_never_overstates_an_unused_target_grant(self):
        state = dict(allowances=dict(notes=6000, targets=[dict(handle='t%d' % n,
            characters=6700 + n % 2, used=0) for n in range(2000)], passages=dict(characters=100000, used=0)))
        view = v7.describe_allowances(self.route, state, 18000)
        self.assertEqual(view['unused_target_allowance'], 6700)
        self.assertLessEqual(capacity.measure_capacity_value(self.route, view), 30000)

    def test_list_page_response_uses_cursor_after_the_current_delivery(self):
        packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state = json.loads(self.artifacts.read(chain['state']))
        current = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', 1,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading',
            requests=[dict(id='page', kind='list_page', list='allowances', after=None)])
        self.assertIsNone(current['reading_turn']['plan']['now'][0]['page']['next'])
        answer = self.answer(current)
        answer['passage_requests'] = [dict(id='repeat', kind='list_page', list='allowances', after=None)]
        with self.assertRaises(ResearchError):
            v7.validate_turn_output(current, answer, artifacts=self.artifacts,
                                   assignment=dict(reading_chain=chain, capacity_plan=chain['capacity_plan']))

    def test_quoted_notes_and_undeliverable_passages_are_refused_at_response_boundary(self):
        packet, chain = self.prepare()
        assignment = dict(reading_chain=chain, capacity_plan=chain['capacity_plan'])
        for notes, requests in [('\"' * packet['reading_turn']['allowances']['notes'], []),
                                ('Notes.', [dict(id='bad', unit='b999', anchor='text', characters=1000)]),
                                ('Notes.', [dict(id='bad', unit='b1', anchor='absent anchor', characters=1000)]),
                                ('Notes.', [dict(id='p%d' % n, unit='b1', anchor='abcde', characters=100000) for n in range(10)])]:
            answer = self.answer(packet, notes)
            answer['passage_requests'] = requests
            with self.subTest(notes=notes[:10], requests=len(requests)), self.assertRaises(ResearchError):
                v7.validate_turn_output(packet, answer, artifacts=self.artifacts, assignment=assignment)

    def test_large_list_page_fits_its_serialized_material_representation(self):
        packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state = json.loads(self.artifacts.read(chain['state']))
        plan['targets']['handles'] = ['t%d' % n for n in range(2000)]
        state['allowances']['targets'] = [dict(handle=handle, characters=30000, used=0) for handle in plan['targets']['handles']]
        limits = capacity.derive_plan_task_limits(plan, 'reading', [dict(material_allowance=15000)])
        current = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', 1, inventory, state,
            plan, limits, 'reading', requests=[dict(id='page', kind='list_page', list='allowances', after=None)])
        self.assertEqual(current['reading_turn']['plan']['now'][0]['kind'], 'list_page')
        cost = capacity.measure_capacity_value(self.route, current['reading_turn']['plan']['now'])
        cost += capacity.measure_capacity_value(self.route, current['evidence'])
        self.assertLessEqual(cost, 15000)

    def test_gap_bodies_are_units_and_non_ascii_material_fits_token_bound(self):
        many_gaps = build_native()
        many_gaps['scientific_context']['minimum_gaps'] = [dict(
            formulation=many_gaps['scientific_context']['openness_minimum']['statement'], text='Gap %d' % n)
            for n in range(2000)]
        with patch.object(reading, 'list_reading_rows', return_value=[]):
            built = v7.build_inventory({}, self.artifacts, many_gaps)
        gap_unit = next(row for row in built['inventory']['units']
                        if row['identity'].get('record_kind') == 'minimum_gaps')
        self.assertEqual(len(json.loads(built['bodies'][gap_unit['sha256']])), 2000)
        with patch.object(v7, 'build_inventory', return_value=built):
            refused = v7.check_v7_admission({}, self.artifacts, self.route, many_gaps)
        self.assertEqual(refused['state'], 'stop')
        self.route = build_route(effort='xhigh')
        self.native['scientific_context']['minimum_gaps'] = [dict(
            formulation=self.native['scientific_context']['openness_minimum']['statement'], text='One bounded gap.')]
        self.built = build_inventory(self.artifacts, 100000, unicode=True)
        packet, chain = self.prepare()
        self.assertNotIn('minimum_gaps', packet['reading_turn'])
        self.assertEqual(packet['reading_turn']['minimum']['gaps'], 1)
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        self.assertEqual(plan['measure']['rule'], 'bytes')
        from research_harness.review_protocol import canonical_prompt
        from research_harness.review_transport import request_body
        request = request_body(self.route, canonical_prompt('native_math', reading_stage='reading',
            reading_protocol=v7.PROTOCOL)['text'], packet)
        self.assertTrue(capacity.describe_plan_preflight({}, self.route, request, plan, 'reading')['plan_comparison']['fits'])

    def test_q1_every_allowance_shortest_and_longest_excerpts_reaches_final_after_sixty_turns(self):
        from research_harness import review_protocol as protocol
        for width in (1, 6000):
            self.built = build_inventory(self.artifacts, 910000)
            with patch.object(capacity, 'read_profile_priors', return_value=[]):
                packet, chain = self.prepare()
            plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
            inventory = json.loads(self.artifacts.read(chain['inventory']))
            turns = []
            state = json.loads(self.artifacts.read(chain['state']))
            for number in range(1, 100):
                kind = 'reading' if v7._find_remaining_ranges(inventory, state) else 'final'
                limits = capacity.derive_plan_task_limits(plan, kind)
                packet = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', number,
                    inventory, state, plan, limits, kind)
                request = protocol.transport.request_body(self.route,
                    protocol.canonical_prompt('native_math', reading_stage=kind, reading_protocol=v7.PROTOCOL)['text'], packet)
                self.assertTrue(capacity.describe_plan_preflight({}, self.route, request, plan, kind)['plan_comparison']['fits'])
                if kind == 'final':
                    self.assertGreater(number, 60)
                    break
                binding = packet['reading_turn']
                answer = self.answer(packet, 'n' * binding['allowances']['notes'])
                remaining = {row['handle']: row['remaining'] for row in binding['allowances']['targets']}
                # Each excerpt includes its full serialized entry cost. Keep
                # adding until the turn's excerpt allowance or a target is full.
                cost = 0
                for row, body in zip(binding['plan']['now'], packet['evidence']):
                    if row['kind'] == 'list_page':
                        continue
                    for target in remaining:
                        while remaining[target] > 600 and cost < binding['allowances']['excerpts']:
                            value = dict(unit=row['handle'], text=body['content'][:min(width, max(1, remaining[target] - 600))],
                                         purpose='A relevant range.', targets=[target])
                            charge = capacity.measure_capacity_value(self.route, dict(kind='excerpt', unit=row['handle'],
                                text=value['text'], purpose=value['purpose'], targets=[target], start=row['start'],
                                end=row['start'] + len(value['text']), sha256='x' * 64, status='verified', handle='e999999999'))
                            if charge > remaining[target] or cost + charge > binding['allowances']['excerpts']:
                                break
                            answer['excerpts'].append(value)
                            remaining[target] -= charge
                            cost += charge
                v7.validate_turn_output(packet, answer)
                turns.append(v7.project_own_turn(self.artifacts, self.route, packet, answer,
                    'assignment-' + str(number).zfill(49), 'attempt-' + str(number).zfill(52)))
                state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
            else:
                self.fail('The bounded synthetic chain must reach its final.')

    def test_q1_many_own_gaps_keep_artifacts_without_growing_final_past_grants(self):
        self.built = build_inventory(self.artifacts, 910000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state, turns = json.loads(self.artifacts.read(chain['state'])), []
        for number in range(1, 100):
            kind = 'reading' if v7._find_remaining_ranges(inventory, state) else 'final'
            packet = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', number,
                inventory, state, plan, capacity.derive_plan_task_limits(plan, kind), kind)
            if kind == 'final':
                break
            answer = self.answer(packet)
            answer['gaps'] = [dict(formulation='t1', text='g' * 600) for _ in range(10)]
            v7.validate_turn_output(packet, answer)
            turns.append(v7.project_own_turn(self.artifacts, self.route, packet, answer, 'turn-%d' % number,
                                             'try-%d' % number, state=state))
            state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
        self.assertTrue(any(row['status'] == 'over_allowance' for row in state['gaps']))
        self.assertTrue(all(row['text'] == 'g' * 600 for row in packet['reading_final']['gaps']))

    def test_q8_ten_requested_passages_finish_over_multiple_turns_without_losing_ranges(self):
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state = json.loads(self.artifacts.read(chain['state']))
        request = self.answer(packet)
        request['passage_requests'] = [dict(id='p%d' % n, unit=inventory['material'][0]['handle'],
                                          anchor='abcde', characters=10000) for n in range(10)]
        turns = [v7.project_own_turn(self.artifacts, self.route, packet, request, 'first', 'attempt', state=state)]
        state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
        self.assertEqual(len(state['pending_requests']), 10)
        received = []
        for number in range(2, 30):
            packet = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', number,
                inventory, state, plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading',
                requests=state['pending_requests'])
            received += [row['characters'] for row in packet['reading_turn']['plan']['now'] if row.get('request')]
            answer = self.answer(packet)
            turns.append(v7.project_own_turn(self.artifacts, self.route, packet, answer, 't%d' % number, 'a%d' % number, state=state))
            state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
            if not state['pending_requests']:
                break
        self.assertEqual(sum(received), 100000)
        self.assertGreater(number, 3)

    def test_passage_and_fresh_range_of_one_unit_both_support_excerpts(self):
        text = 'a' * 40000 + 'UNIQUE' + 'b' * 40000
        content = text.encode()
        reference = self.artifacts.put(content, 'text/plain')
        self.built = v7.plan_inventory([dict(kind='fulltext', version_id='work', text_sha256=reference['sha256'], artifact=reference)],
                                       {reference['sha256']: content})
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state = json.loads(self.artifacts.read(chain['state']))
        current = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', 1, inventory, state,
            plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading',
            requests=[dict(id='passage', unit=inventory['material'][0]['handle'], anchor='UNIQUE', characters=1000)])
        answer = self.answer(current)
        answer['excerpts'] = [dict(unit=inventory['material'][0]['handle'], text='UNIQUE', purpose='A decisive passage.', targets=['t1'])]
        v7.validate_turn_output(current, answer)
        projection = v7.project_own_turn(self.artifacts, self.route, current, answer, 't', 'a', state=state)
        self.assertEqual(projection['entries'][0]['start'], 40000)

    def test_denser_own_measurement_lowers_reading_while_final_still_fits(self):
        from research_harness import review_failure_class
        from test_review_capacity_closure import build_capacity_records
        witnessed = build_capacity_records(self.route, tokens=487900, capacity_probe=True)
        with patch.object(v7, 'build_inventory', return_value=self.built):
            packet, chain = v7.prepare_first_assignment(witnessed, self.artifacts, self.value,
                                                       self.route, self.packet, [])
        assignment = dict(self.value, capacity_plan=chain['capacity_plan'],
                          capacity_plan_kind='reading', reading_chain=dict(first_assignment_id='chain', turn=1), recorded_revision=1)
        records = dict(witnessed, review_assignment={'chain': assignment}, review_route={'route': self.route}, review_attempt={
            'dense': dict(id='dense', assignment_id='chain', recorded_revision=2,
                task=dict(kind='reading', level_material_allowance=120000),
                usage=dict(sends=1, request_characters=120000, input_tokens=250000, output_tokens=50000))})
        with patch.object(review_failure_class, 'classify_attempt', return_value=None):
            limits = capacity.derive_turn_task_limits(records, self.artifacts, assignment)
        # Give the longer prompt its room while keeping a dense ratio that lowers this turn to 30,000.
        self.assertEqual(limits['material_allowance'], 30000)
        self.assertEqual(capacity.derive_turn_task_limits(records, self.artifacts, assignment,
                                                        kind='final')['material_allowance'], 60000)

    def test_exact_target_and_notes_grants_reserve_final_collection_overhead(self):
        from test_review_output_profile import build_row
        rows, bodies = [], {}
        for index, char in enumerate('abc'):
            content = (char * 20000).encode()
            reference = self.artifacts.put(content, 'text/plain')
            rows.append(dict(kind='fulltext', version_id='work-%d' % index, text_sha256=reference['sha256'], artifact=reference))
            bodies[reference['sha256']] = content
        gaps = self.artifacts.put_json([])
        rows.append(dict(kind='record', record_kind='minimum_gaps', record_id='minimum_gaps', artifact=gaps))
        bodies[gaps['sha256']] = b'[]'
        self.built = v7.plan_inventory(rows, bodies)
        with patch.object(capacity, 'read_profile_priors', return_value=[build_row(level=None, thinking=1000)]):
            packet, chain = self.prepare()
        plan = capacity.read_chain_capacity_plan(self.artifacts, chain)
        inventory = json.loads(self.artifacts.read(chain['inventory']))
        state = json.loads(self.artifacts.read(chain['state']))
        turns = []
        def fill_target(packet, answer, target, row_index):
            row, body = packet['reading_turn']['plan']['now'][row_index], packet['evidence'][row_index]
            allowance = next(row['remaining'] for row in packet['reading_turn']['allowances']['targets'] if row['handle'] == target)
            def measure_width(width):
                entry = dict(kind='excerpt', handle='e999999999', unit=row['handle'], text=body['content'][:width],
                    purpose='p' * 200, targets=[target], start=row['start'], end=row['start'] + width, sha256='x' * 64, status='verified')
                return capacity.measure_capacity_value(self.route, entry) + capacity.measure_capacity_value(self.route,
                    {key: row[key] for key in ('handle', 'kind', 'label')})
            low, high = 1, len(body['content'])
            while low < high:
                middle = (low + high + 1) // 2
                if measure_width(middle) <= allowance:
                    low = middle
                else:
                    high = middle - 1
            self.assertEqual(measure_width(low), allowance)
            answer['excerpts'].append(dict(unit=row['handle'], text=body['content'][:low], purpose='p' * 200, targets=[target]))
        answer = self.answer(packet, 'n' * packet['reading_turn']['allowances']['notes'])
        fill_target(packet, answer, 't1', 0)
        fill_target(packet, answer, 't2', 1)
        turns.append(v7.project_own_turn(self.artifacts, self.route, packet, answer, 'first', 'a1', state=state))
        state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
        final = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', 2, inventory, state,
                                    plan, capacity.derive_plan_task_limits(plan, 'final'), 'final')
        request = self.answer(dict(reading_turn=final['reading_final']), 'n' * state['allowances']['notes'])
        third = inventory['material'][2]['handle']
        request['passage_requests'] = [dict(id='third', unit=third, anchor='c', characters=20000)]
        turns.append(v7.project_own_turn(self.artifacts, self.route, final, request, 'request', 'a2', state=state))
        state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
        packet = v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', 3, inventory, state,
            plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading', requests=state['pending_requests'])
        answer = self.answer(packet, 'n' * state['allowances']['notes'])
        fill_target(packet, answer, 't3', 0)
        turns.append(v7.project_own_turn(self.artifacts, self.route, packet, answer, 'last-read', 'a3', state=state))
        state = v7.build_chain_state(inventory, turns, route=self.route, plan=plan)['state']
        self.assertEqual(state['allowances']['notes'] + sum(row['used'] for row in state['allowances']['targets']), 60000)
        v7.build_turn_packet(self.artifacts, self.route, self.packet, 'chain', 4, inventory, state,
                             plan, capacity.derive_plan_task_limits(plan, 'final'), 'final')


from openness_reading_fixtures import OpennessReadingCase


class V7CommandTests(OpennessReadingCase):
    # Leave 359,900 input tokens after the configured answer reservation.
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)

    def setUp(self):
        super().setUp()
        patcher = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_v7_answer(self, packet):
        binding = packet.get('reading_turn', packet.get('reading_final'))
        return dict(schema_version=2, stage='reading', notes='The delivered units have been read.',
                    excerpts=[], gaps=[], passage_requests=[],
                    **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})

    def finish_reading(self, native):
        self.assign('turn-1', native)
        current = 'turn-1'
        for number in range(1, 100):
            packet = self.read_packet(current)
            if 'reading_final' in packet:
                return current
            self.invoke(current, self.build_v7_answer(packet))
            following = 'turn-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        self.fail('An admitted v7 chain must finish reading.')

    def build_v7_finding(self, packet, handle='n1'):
        binding = packet['reading_final']
        return dict(schema_version=3, **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
            findings=dict({key: 'Assessed the supplied subject.' for key in
                ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                openness=dict(outcome='unresolved', coverage='Reviewed every required unit.', reason='A remaining gap.',
                    grounds=[dict(kind='reviewer_gap', text='The recorded material holds the detail.', refs=[dict(ref=handle)])])))

    def test_material_parts_finish_and_unknown_handles_are_retained_as_refusals(self):
        from research_harness import review_protocol as protocol
        native = self.export()
        current = self.finish_reading(native)
        self.invoke(current, dict(stage='material', title='A reviewer argument', part=1, text='Part one.', more=True))
        self.assign('material-next', native, prior=current)
        packet = self.read_packet('material-next')
        self.assertEqual(packet['reading_final']['material_request']['part'], 2)
        self.invoke('material-next', dict(stage='material', title='A reviewer argument', part=2, text='Part two.', more=False))
        self.assign('finding', native, prior='material-next')
        packet = self.read_packet('finding')
        self.assertEqual(packet['reading_final']['material'][0]['handle'], 'm1')
        response = self.invoke('finding', self.build_v7_finding(packet, 'm999999'))
        self.assertEqual(response['output_refusal']['code'], 'invalid_reference_handle')
        self.invoke('finding', self.build_v7_finding(packet, 'm1'), attempt='valid-finding')
        records = self.store.snapshot()['records']
        self.assertTrue(protocol.assignment_state(records, self.artifacts, 'finding')['ready'])

    def test_a13_real_commands_reach_one_request_final_with_expanded_handles(self):
        from research_harness import review_protocol as protocol
        native = self.export()
        receipt = self.assign('turn-1', native)
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        current = 'turn-1'
        for number in range(1, 70):
            packet = self.read_packet(current)
            if 'reading_final' in packet:
                break
            self.invoke(current, self.build_v7_answer(packet))
            following = 'turn-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        else:
            self.fail('The admitted v7 chain must reach its final.')
        binding = packet['reading_final']
        answer = dict(schema_version=3, **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
            findings=dict({key: 'Assessed the supplied subject.' for key in
                ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                openness=dict(outcome='unresolved', coverage='Reviewed every required unit.', reason='A remaining gap.',
                    grounds=[dict(kind='reviewer_gap', text='A gap recorded in notes.', refs=[dict(ref='n1')])])))
        answer['findings']['openness']['coverage'] = [dict(target=target, searches='all supplied searches')
            for target in packet['reading_final']['page']['targets']]
        self.invoke(current, answer)
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, current)
        self.assertTrue(state['ready'], state['obligations'])
        reference = state['output']['findings']['openness']['grounds'][0]['refs'][0]
        self.assertIn('sha256', reference)
        attempt = records['review_attempt'][state['attempt_id']]
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, records['review_assignment'][current], attempt))

    def test_q8_list_page_request_id_survives_a_capacity_replacement(self):
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        native = self.export()
        with patch.object(capacity, 'read_profile_priors', return_value=[build_row(level=None, thinking=1000, count=8)]):
            self.assign('turn-1', native)
            answer = self.build_v7_answer(self.read_packet('turn-1'))
            answer['passage_requests'] = [dict(id='custom-list-id', kind='list_page', list='allowances', after=None)]
            self.invoke('turn-1', answer)
            self.assign('with-page', native, prior='turn-1')
            def respond(route, request, credential):
                response = build_codex_response(route, request, {})
                response.update(status='incomplete')
                response['usage']['output_tokens'] = 138371
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='failed-page', assignment_id='with-page'), credential='fixture')
            records = self.store.snapshot()['records']
            recovery = capacity.plan_capacity_recovery(records, self.artifacts, records['review_assignment']['with-page'], 'output_over_cap')
            self.assertEqual(recovery['kind'], 'replace')
            replacement = self.mutate(protocol.record_assignment, recovery['payload'])['result']['id']
            packet = self.read_packet(replacement)
            page = next(row for row in packet['reading_turn']['plan']['now'] if row['kind'] == 'list_page')
            self.assertEqual(page['request'], 'custom-list-id')
            self.invoke(replacement, self.build_v7_answer(packet))
            self.assign('after-page', native, prior=replacement)
            packet = self.read_packet('after-page')
            self.assertFalse(any(row['kind'] == 'list_page' for row in packet.get('reading_turn', {}).get('plan', {}).get('now', [])))

    def test_q4_output_limit_replacements_use_every_lower_level_on_same_route(self):
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        native = self.export()
        with patch.object(capacity, 'read_profile_priors', return_value=[build_row(level=None, thinking=1000, count=8, over=1)]):
            self.assign('turn-1', native)
            current = 'turn-1'
            for number, allowance in enumerate((120000, 60000, 30000, 15000)):
                packet = self.read_packet(current)
                self.assertEqual(packet['reading_turn']['capacity']['material_allowance'], allowance)
                def respond(route, request, credential):
                    response = build_codex_response(route, request, self.build_v7_answer(packet))
                    response['status'] = 'incomplete'
                    response['usage']['output_tokens'] = 138371
                    return bind_codex_response(route, request, response)
                with patch.object(protocol.transport, 'send_request', side_effect=respond):
                    self.mutate(protocol.invoke_assignment, dict(id='over-%d' % number, assignment_id=current), credential='fixture')
                records = self.store.snapshot()['records']
                recovery = capacity.plan_capacity_recovery(records, self.artifacts, records['review_assignment'][current], 'output_over_cap')
                self.assertEqual(len(recovery['details']['attempts']), number + 1)
                if allowance == 15000:
                    self.assertEqual(recovery['kind'], 'decision')
                    self.assertIn('range_not_served', recovery['details'])
                    break
                self.assertEqual(recovery['kind'], 'replace')
                self.assertEqual(recovery['payload']['route_id'], 'route')
                self.assertTrue(recovery['preflight']['plan_comparison']['fits'])
                result = self.mutate(protocol.record_assignment, recovery['payload'])['result']
                current = result['id']
                self.assertEqual(result['route_digest'], records['review_assignment']['turn-1']['route_digest'])

    def test_check_and_assignment_use_same_ratio_and_write_check_nothing(self):
        from research_harness import review_protocol as protocol
        from research_harness.check_mode import check_mutation
        native = self.export()
        payload = dict(id='turn-1', route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                       author_id='problem-author', context=dict(native_packet=native))
        revision = self.store.revision
        checked = check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                                 request_id='v7-check', bounded_payload=payload)
        self.assertEqual(self.store.revision, revision)
        actual = self.assign('turn-1', native)
        stored = capacity.read_chain_capacity_plan(self.artifacts, actual)
        self.assertEqual(checked['capacity_plan'], stored)
        self.assertEqual(checked['protocol_selection'], actual['protocol_selection'])
        self.assertTrue(actual['capacity_warning']['input']['request']['fits'])

    def test_q5_failed_final_exports_and_successor_keeps_ratio_without_carried_readings(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from research_harness.check_mode import check_mutation
        from codex_route_fixtures import build_codex_response, bind_codex_response
        native = self.export()
        self.assign('turn-1', native)
        current = 'turn-1'
        for number in range(1, 50):
            packet = self.read_packet(current)
            if 'reading_final' in packet:
                break
            self.invoke(current, self.build_v7_answer(packet))
            following = 'turn-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response.update(status='incomplete')
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 5
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.mutate(protocol.invoke_assignment, dict(id='dense-final', assignment_id=current), credential='fixture')
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, current)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'export'))
        self.assertEqual(plan['details']['ratio']['source']['attempt'], 'dense-final')
        next_native = self.export(name='successor-packet', assignment_id='successor')
        payload = dict(id='successor', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=next_native))
        checked = check_mutation(self.store, protocol.record_assignment, payload, expected_revision=self.store.revision,
                                 request_id='dense-check', bounded_payload=payload)
        actual = self.assign('successor', next_native)
        self.assertEqual(checked['protocol_selection'], actual['protocol_selection'])
        self.assertEqual(actual['protocol_selection']['protocol'], reading.PROTOCOL_V6)
        refusal = actual['protocol_selection']['admissions'][0]
        self.assertEqual(refusal['measure']['source'], dict(chain='turn-1', attempt='dense-final'))
        self.assertEqual(refusal['measure']['characters_per_token'], .19)

    def test_q5_dense_failed_final_successor_admits_v7_and_third_chain_keeps_applied_ratio(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from research_harness.check_mode import check_mutation
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        native = json.loads(self.artifacts.read(self.export()))
        native['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour %d.' % n for n in range(2)]
        reference = protocol._put_json(self.artifacts, native)
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            current = self.finish_reading(reference)
            original = self.store.snapshot()['records']['review_assignment'][current]
            original_plan = capacity.read_chain_capacity_plan(self.artifacts, original)
            def respond(route, request, credential):
                response = build_codex_response(route, request, {})
                response['status'] = 'incomplete'
                response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 2
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='dense-final', assignment_id=current), credential='fixture')
            handoff = plan_next(self.store.snapshot()['records'], self.artifacts, current)
            self.assertEqual(handoff['command'], 'export')
            self.assertEqual(handoff['details']['step']['command'], 'export')
            for identifier in ('successor', 'third'):
                successor = self.export(name=identifier + '-packet', assignment_id=identifier)
                successor_native = json.loads(self.artifacts.read(successor))
                successor_native['scientific_context']['openness_minimum']['neighbours'] = native['scientific_context']['openness_minimum']['neighbours']
                successor = protocol._put_json(self.artifacts, successor_native)
                payload = dict(id=identifier, route_id='route', dossier_id=None, role='native_math',
                    reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=successor))
                checked = check_mutation(self.store, protocol.record_assignment, payload,
                    expected_revision=self.store.revision, request_id=identifier + '-check', bounded_payload=payload)
                actual = self.assign(identifier, successor)
                self.assertEqual(checked['protocol_selection'], actual['protocol_selection'])
                self.assertEqual(actual['protocol_selection']['protocol'], v7.PROTOCOL, actual['protocol_selection'])
                stored = self.store.snapshot()['records']['review_assignment'][identifier]
                plan = capacity.read_chain_capacity_plan(self.artifacts, stored)
                self.assertEqual(plan['measure']['characters_per_token'], .47)
                self.assertEqual(plan['measure']['source'], dict(chain='turn-1', attempt='dense-final'))
                self.assertLess(plan['measure']['characters_per_token'], original_plan['measure']['characters_per_token'])
                self.assertEqual(v7.check_v7_admission(self.store.snapshot()['records'], self.artifacts,
                    self.store.snapshot()['records']['review_route']['route'], successor_native)['state'], 'record')

    def test_q5_dense_failed_final_successor_refuses_both_protocols_before_call(self):
        from research_harness import review_protocol as protocol
        from research_harness.review_continuation import plan_next
        from research_harness.check_mode import check_mutation
        from codex_route_fixtures import build_codex_response, bind_codex_response
        native = self.export()
        current = self.finish_reading(native)
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 5
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.mutate(protocol.invoke_assignment, dict(id='dense-final', assignment_id=current), credential='fixture')
        self.assertEqual(plan_next(self.store.snapshot()['records'], self.artifacts, current)['command'], 'export')
        successor = json.loads(self.artifacts.read(self.export(name='successor-packet', assignment_id='successor')))
        successor['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour %d. ' % n + 'x' * 300 for n in range(4999)]
        reference = protocol._put_json(self.artifacts, successor)
        payload = dict(id='successor', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
        refusals = []
        for check in (True, False):
            with self.assertRaises(ResearchError) as error:
                if check:
                    check_mutation(self.store, protocol.record_assignment, payload,
                        expected_revision=self.store.revision, request_id='successor-check', bounded_payload=payload)
                else:
                    self.assign('successor', reference)
            self.assertEqual(error.exception.code, 'review_route_capacity_below_floor')
            refusals.append(error.exception.details['admissions'])
        self.assertEqual(refusals[0], refusals[1])
        self.assertEqual(refusals[0][0]['measure']['source'], dict(chain='turn-1', attempt='dense-final'))
        self.assertNotIn('successor', self.store.snapshot()['records']['review_assignment'])

    def test_real_commands_refuse_a_fork_and_replacement_of_a_successful_turn(self):
        from research_harness import review_protocol as protocol
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_v7_answer(self.read_packet('turn-1')))
        self.assign('turn-2', native, prior='turn-1')
        with self.assertRaises(ResearchError) as error:
            self.assign('fork', native, prior='turn-1')
        self.assertEqual(error.exception.code, 'review_source_continuation_fork')
        with self.assertRaises(ResearchError):
            self.mutate(protocol.record_assignment, dict(id='replay', route_id='route', dossier_id=None,
                role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                context=dict(native_packet=native, reading_replacement='turn-1')))

    def test_q7_q8_five_thousand_targets_refuse_both_admissions_before_writing(self):
        from research_harness import review_protocol as protocol
        from research_harness.check_mode import check_mutation
        native = json.loads(self.artifacts.read(self.export()))
        native['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour %d. ' % n + 'x' * 300 for n in range(4999)]
        reference = protocol._put_json(self.artifacts, native)
        payload = dict(id='turn-1', route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                       author_id='problem-author', context=dict(native_packet=reference))
        revision = self.store.revision
        with self.assertRaises(ResearchError) as error:
            check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                           request_id='large-check', bounded_payload=payload)
        self.assertEqual(error.exception.code, 'review_route_capacity_below_floor')
        details = error.exception.details
        self.assertEqual([row['protocol'] for row in details['admissions']], [v7.PROTOCOL, reading.PROTOCOL_V6])
        self.assertEqual(self.store.revision, revision)
        self.assertIn('owner_decides', details['decision'])
        self.assertTrue(details['unserved_range'])
        self.assertEqual(details['largest_one_request_targets'], 8)
        smaller_step = details['decision']['then'][0]['steps'][0]
        self.assertEqual(smaller_step['command'], 'export')
        self.assertEqual(smaller_step['then'][0]['check'], True)
        smaller = self.export(name='smaller-packet', assignment_id='smaller')
        smaller_payload = dict(payload, id='smaller', context=dict(native_packet=smaller))
        check_mutation(self.store, protocol.record_assignment, smaller_payload, expected_revision=self.store.revision,
                       request_id='smaller-check', bounded_payload=smaller_payload)
        self.assign('smaller', smaller)
        from research_harness.review_continuation import plan_next
        self.assertEqual(plan_next(self.store.snapshot()['records'], self.artifacts, 'smaller')['state'], 'run')

    def test_q7_dense_unrelated_reviewer_falls_back_with_identical_v6_packet_and_prompt(self):
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness.problem_openness import export_openness
        old_native = export_openness(self.store, dict(assignment_id='older', reviewer_id='another-reviewer',
            minimum=dict(searches=[dict(formulation=self.selected['statement'], search_id=self.search['id'])],
                         settling_work_id=None, duplicate_resolutions=[])), self.root / 'older-packet')['native_packet']
        self.mutate(protocol.record_assignment, dict(id='older', route_id='route', dossier_id=None, role='native_math',
            reviewer_id='another-reviewer', author_id='problem-author', context=dict(native_packet=old_native)))
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 5
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.mutate(protocol.invoke_assignment, dict(id='unrelated-dense', assignment_id='older'), credential='fixture')
        native = self.export()
        records = self.store.snapshot()['records']
        value = dict(id='turn-1', route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                     author_id='problem-author', context=dict(native_packet=native))
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer', native_packet=native)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
            expected, _ = reading.prepare_packet(records, self.artifacts, value, records['review_route']['route'], logical)
        actual = self.assign('turn-1', native)
        self.assertEqual(actual['protocol_selection']['protocol'], reading.PROTOCOL_V6)
        self.assertEqual(self.read_packet('turn-1'), expected)
        self.assertEqual(actual['prompt'], protocol.canonical_prompt('native_math', reading_stage='reading', reading_protocol=reading.PROTOCOL_V6))

    def test_q7_eight_targets_start_v7_and_nine_start_byte_identical_v6(self):
        from research_harness import review_protocol as protocol
        from research_harness.problem_openness import export_openness
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        for count in (8, 9):
            self.selected = self.select_statement(identifier='statement-%d' % count, candidate_id='candidate-1',
                neighbours=['Neighbour %d.' % n for n in range(count - 1)])
            bindings = [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
            for formulation in self.selected['neighbours']:
                search = ProblemOpennessMinimumTests.search(self, formulation=formulation)
                bindings.append(dict(formulation=formulation, search_id=search['id']))
            identifier = 'head-%d' % count
            native = export_openness(self.store, dict(assignment_id=identifier, reviewer_id='openness-reviewer',
                minimum=dict(searches=bindings, settling_work_id=None, duplicate_resolutions=[])),
                self.root / ('packet-%d' % count))['native_packet']
            records = self.store.snapshot()['records']
            value = dict(id=identifier, route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                         author_id='problem-author', context=dict(native_packet=native))
            logical = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer', native_packet=native)
            if count == 9:
                with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
                    expected, _ = reading.prepare_packet(records, self.artifacts, value, records['review_route']['route'], logical)
            actual = self.assign(identifier, native)
            self.assertEqual(actual['protocol_selection']['protocol'], v7.PROTOCOL if count == 8 else reading.PROTOCOL_V6)
            if count == 9:
                self.assertEqual(self.read_packet(identifier), expected)
                refusal = actual['protocol_selection']['admissions'][0]
                self.assertEqual((refusal['reason'], refusal['largest_one_request_targets']), ('final_needs_pages', 8))

    def test_max_138371_output_tokens_halves_material_on_the_same_claude_route(self):
        from research_harness import review_protocol as protocol, review_claude_transport as claude
        from test_research_claude_transport import clean_stream, completed
        from test_review_output_profile import build_row
        route = build_route(effort='max')
        route['id'] = 'max-route'
        def respond(selected, request, credential):
            packet = reading.review_excerpts.read_sent_packet(request)
            output = (dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])
                      if 'allowed_control' in packet else self.build_v7_answer(packet))
            events = clean_stream(request, output=json.dumps(output), model=route['model'])
            events[-1]['usage']['output_tokens'] = 900 if 'allowed_control' in packet else 138371
            with patch.object(claude, '_run_owned', return_value=completed(events)):
                return claude.send_request(selected, request, credential)
        with patch.object(claude, '_cli', return_value=('/fixture/claude', 'fixture-version')), patch.object(
                protocol.transport, 'send_request', side_effect=respond), patch.object(capacity, 'read_profile_priors',
                return_value=[build_row(level=None, count=8, over=1, thinking=1000)]):
            self.mutate(protocol.record_route, route)
            self.mutate(protocol.probe_route, dict(id='max-probe', route_id='max-route'), credential='fixture')
            native = self.export()
            self.mutate(protocol.record_assignment, dict(id='turn-1', route_id='max-route', dossier_id=None,
                role='native_math', reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=native)))
            self.mutate(protocol.invoke_assignment, dict(id='max-over', assignment_id='turn-1'), credential='fixture')
            records = self.store.snapshot()['records']
            recovery = capacity.plan_capacity_recovery(records, self.artifacts, records['review_assignment']['turn-1'], 'output_over_cap')
            self.assertEqual(recovery['kind'], 'replace')
            self.assertEqual(recovery['details']['material_allowance'], 60000)
            result = self.mutate(protocol.record_assignment, recovery['payload'])['result']
            self.assertEqual(result['route_id'], 'max-route')
            self.assertEqual(result['prompt']['parameters']['capacity']['material_allowance'], 60000)

    def test_material_recovery_halves_answer_bound_and_stops_at_its_floor(self):
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        rows = [build_row(level=None, count=8, over=1, thinking=1000),
                build_row(kind='final', level=240000, count=8, over=0, thinking=1000),
                build_row(kind='final', level=None, count=8, over=1, thinking=1000)]
        with patch.object(capacity, 'read_profile_priors', return_value=rows):
            native = self.export()
            current = self.finish_reading(native)
            self.invoke(current, dict(stage='material', title='An argument', part=1, text='First part.', more=True))
            self.assign('material-pending', native, prior=current)
            current, seen = 'material-pending', []
            for number in range(5):
                packet = self.read_packet(current)
                seen.append(packet['reading_final']['capacity']['answer_bound'])
                def respond(route, request, credential):
                    response = build_codex_response(route, request, {})
                    response.update(status='incomplete')
                    response['usage']['output_tokens'] = 138371
                    return bind_codex_response(route, request, response)
                with patch.object(protocol.transport, 'send_request', side_effect=respond):
                    self.mutate(protocol.invoke_assignment, dict(id='material-over-%d' % number, assignment_id=current), credential='fixture')
                records = self.store.snapshot()['records']
                recovery = capacity.plan_capacity_recovery(records, self.artifacts, records['review_assignment'][current], 'output_over_cap')
                if recovery['kind'] == 'decision':
                    break
                current = self.mutate(protocol.record_assignment, recovery['payload'])['result']['id']
            self.assertEqual(seen, [21500, 10750, 7500])

    def test_valid_complete_answer_twice_its_bound_keeps_over_allowance_entries_in_state(self):
        from research_harness import review_protocol as protocol
        native = self.export()
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        answer = self.build_v7_answer(packet)
        answer['notes'] = 'n' * packet['reading_turn']['allowances']['notes']
        row, body = packet['reading_turn']['plan']['now'][0], packet['evidence'][0]
        value = dict(unit=row['handle'], text=body['content'][:1200], purpose='p' * 200, targets=['t1'])
        answer['excerpts'] = []
        while len(json.dumps(answer)) <= 2 * packet['reading_turn']['capacity']['answer_bound']:
            answer['excerpts'].append(value)
        response = self.invoke('turn-1', answer)
        self.assertNotIn('output_refusal', response)
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-turn-1']
        self.assertGreater(attempt['answer']['characters'], 2 * attempt['answer']['bound'])
        self.assertTrue(protocol.assignment_state(records, self.artifacts, 'turn-1')['ready'])
        self.assign('turn-2', native, prior='turn-1')
        assignment = self.store.snapshot()['records']['review_assignment']['turn-2']
        state = json.loads(self.artifacts.read(assignment['reading_chain']['state']))
        self.assertTrue(any(row['status'] == 'over_allowance' for row in state['entries']))

    def test_thirty_failed_sixty_character_attempt_ids_leave_the_exact_request_unchanged(self):
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response, bind_codex_response
        native = self.export()
        self.assign('turn-1', native)
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response.update(status='incomplete')
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            for number in range(30):
                self.mutate(protocol.invoke_assignment, dict(id='a' + str(number).zfill(59), assignment_id='turn-1'), credential='fixture')
        records = self.store.snapshot()['records']
        requests = [self.artifacts.read(row['request']) for row in records['review_attempt'].values()]
        self.assertEqual(len(requests), 30)
        self.assertTrue(all(value == requests[0] for value in requests))

    def test_series_emits_the_pinned_forecast_after_its_first_call(self):
        from research_harness.review_series import run_series
        from research_harness import review_protocol as protocol
        from codex_route_fixtures import build_codex_response
        native = self.export()
        self.assign('turn-1', native)
        rows = []
        def capture(row):
            rows.append(row)
            raise RuntimeError('report captured')
        def respond(route, request, credential):
            packet = reading.review_excerpts.read_sent_packet(request)
            return build_codex_response(route, request, self.build_v7_answer(packet))
        with patch.object(protocol.transport, 'send_request', side_effect=respond) as send, self.assertRaisesRegex(RuntimeError, 'report captured'):
            run_series(self.store, 'turn-1', emit=capture)
        send.assert_called_once()
        self.assertIn('capacity_forecast', rows[0])
        self.assertIn('input', rows[0]['capacity_warning'])


class IncidentV7CommandTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (60000,) * 29

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[(index % 3) * 10:(index % 3 + 1) * 10],
            formulation=self.selected['statement'] if index % 10 == 0 else 'Neighbour %d.' % (index % 10))
            for index in range(20)]

    def test_a13_incident_twenty_searches_twenty_nine_texts_uses_allowances_and_authenticates_final(self):
        from research_harness import review_protocol as protocol
        from research_harness.problem_openness import export_openness
        from test_review_output_profile import build_row
        self.selected = self.select_statement(identifier='incident-statement', candidate_id='candidate-1',
                                             neighbours=['Neighbour %d.' % n for n in range(1, 10)])
        native = export_openness(self.store, dict(assignment_id='incident-head', reviewer_id='openness-reviewer',
            minimum=dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in self.searches],
                         settling_work_id=None, duplicate_resolutions=[])), self.root / 'incident-packet')['native_packet']
        priors = [build_row(level=None, thinking=1000, count=8), build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), patch.object(
                capacity, 'read_profile_priors', return_value=priors):
            assigned = self.assign('incident-head', native)
            self.assertEqual(assigned['protocol_selection']['protocol'], v7.PROTOCOL)
            current = 'incident-head'
            for number in range(1, 100):
                packet = self.read_packet(current)
                if 'reading_final' in packet:
                    break
                binding = packet['reading_turn']
                answer = V7CommandTests.build_v7_answer(self, packet)
                answer['notes'] = 'n' * binding['allowances']['notes']
                room = binding['allowances']['excerpts']
                remaining = {row['handle']: row['remaining'] for row in binding['allowances']['targets']}
                route = self.store.snapshot()['records']['review_route']['route']
                for row, body in zip(binding['plan']['now'], packet['evidence']):
                    if row['kind'] == 'list_page':
                        continue
                    for target in remaining:
                        while remaining[target] > 1000 and room > 1000:
                            width = min(1 if number == 1 else 6000, len(body['content']), remaining[target] - 1000, room - 1000)
                            text = body['content'][:width]
                            value = dict(unit=row['handle'], text=text, purpose='p' * 200, targets=[target])
                            charge = capacity.measure_capacity_value(route, dict(kind='excerpt', handle='e999999999',
                                unit=row['handle'], text=text, purpose=value['purpose'], targets=[target], start=row['start'],
                                end=row['start'] + len(text), sha256='x' * 64, status='verified'))
                            charge += capacity.measure_capacity_value(route, {key: row[key] for key in ('handle', 'kind', 'label')})
                            if charge > min(room, remaining[target]):
                                break
                            answer['excerpts'].append(value)
                            remaining[target] -= charge
                            room -= charge
                passage_room = binding['allowances']['passages']['characters'] - binding['allowances']['passages']['used']
                passage_room -= sum(row['characters'] for row in binding['plan']['now'] if row.get('request'))
                if passage_room >= 10:
                    row, body = binding['plan']['now'][0], packet['evidence'][0]
                    amount = min(1000, passage_room // 10, len(body['content']))
                    answer['passage_requests'] = [dict(id='p%d-%d' % (number, n), unit=row['handle'],
                        anchor=body['content'][:min(100, amount)], characters=amount) for n in range(10)]
                self.invoke(current, answer)
                following = 'incident-turn-%d' % (number + 1)
                self.assign(following, native, prior=current)
                current = following
            else:
                self.fail('The incident-shaped admitted chain must finish.')
            records = self.store.snapshot()['records']
            assignment = records['review_assignment'][current]
            plan, inventory, state = v7.read_bound_state(self.artifacts, assignment, packet)
            self.assertEqual(len(state['searches']), 20)
            self.assertEqual(sum(row['kind'] == 'fulltext' for row in inventory['units']), 29)
            self.assertEqual(plan['task']['final']['material_allowance'], 480000)
            finding = V7CommandTests.build_v7_finding(self, packet, packet['reading_final']['notes_handle'])
            self.invoke(current, finding)
            records = self.store.snapshot()['records']
            observed = protocol.assignment_state(records, self.artifacts, current)
            self.assertTrue(observed['ready'])
            attempt = records['review_attempt'][observed['attempt_id']]
            self.assertEqual(attempt['task']['targets'], 10)
            recorded = self.mutate(self.load_problem_module().record_openness,
                                  dict(assignment_id=current))['result']
            self.assertEqual(recorded['outcome'], 'unresolved')
            print(json.dumps(dict(incident_v7=dict(turns=number,
                reading_levels=sorted({row['task']['level_material_allowance'] for row in records['review_attempt'].values()
                                       if row['task']['kind'] == 'reading'}),
                reading_answer_bounds=sorted({row['answer']['bound'] for row in records['review_attempt'].values()
                                             if row['task']['kind'] == 'reading'}),
                final_level=plan['task']['final']['material_allowance'], final_answer_bound=plan['task']['final']['answer_bound'],
                frames={kind: row['fields'] for kind, row in plan['frame'].items()},
                final_request=capacity.measure_request(route, json.loads(self.artifacts.read(attempt['request'])),
                                                       records=records, artifacts=self.artifacts))), sort_keys=True))


if __name__ == '__main__':
    unittest.main()
