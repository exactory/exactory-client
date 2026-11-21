"""K4, K6 and K9 obligations of the second reading capacity guard."""

import copy
import json
from pathlib import Path
import unittest

from ci import capacity_guard
from ci.capacity_packet_inventory import read_packet_registry, validate_packet, validate_open_members

ROOT = Path(__file__).resolve().parents[1]


class ReadingCapacityGuardTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('capacity-guard-2', capacity_guard.read_matrix()['installed_units'])

    def test_k5_growing_reading_deliveries_use_distinct_canonical_emissions(self):
        from ci.capacity_packet_inventory import inspect_quantity_factories
        from capacity_packet_fixtures import FACTORIES
        registry = read_packet_registry()
        focused = dict(registry, certificates={}, shipped_quantity_properties=[], roles=dict(native_math=dict(
            members=dict(reading_turn=dict(members=dict(plan={'$ref': 'v7_reading_plan'}))))))
        self.assertEqual(inspect_quantity_factories(focused, capacity_guard.read_matrix(), FACTORIES), [])

    def test_k4_v7_bindings_selection_affected_index_and_unread_are_closed(self):
        registry = read_packet_registry()
        definitions = registry['definitions']
        required = {'v7_reading_binding', 'v7_final_binding', 'v7_carried_selection_ref',
                    'v7_affected_note_index', 'v7_affected_note_row', 'v7_capacity_binding'}
        self.assertTrue(required <= set(definitions), sorted(required - set(definitions)))
        self.assertIn('level_material_allowance', definitions['v7_capacity_binding']['members'])
        self.assertEqual(validate_open_members(registry), [])
        native = registry['roles']['native_math']['protocols']['exactory-independent-review-v1']['members']
        for field, definition in (('reading_turn', 'v7_reading_binding'), ('reading_final', 'v7_final_binding')):
            # A native member is declared inline or by a reference to a shared definition.
            schema = definitions[native[field]['$ref']] if '$ref' in native[field] else native[field]
            for member, declared in definitions[definition]['members'].items():
                actual = schema['members'][member]
                if '$ref' in declared:
                    declared = definitions[declared['$ref']]
                if '$ref' in actual:
                    actual = definitions[actual['$ref']]
                if actual['class'] == 'open':
                    self.assertIn('native_math/exactory-independent-review-v1:' + field + '.' + member,
                                  registry['open_members'])
                else:
                    self.assertEqual(actual['class'], declared['class'], (field, member))
        for name, schema in definitions.items():
            if not name.startswith('v7_'):
                continue
            def require_metadata(declaration, path):
                if '$ref' in declaration:
                    declaration = definitions[declaration['$ref']]
                self.assertIn(declaration.get('class'), ('fixed', 'collection', 'body', 'material', 'carried', 'reference'), path)
                for key in ('quantity', 'bound', 'entry', 'fixture'):
                    self.assertTrue(declaration.get(key), (path, key))
                self.assertIn(declaration['fixture'], registry['fixtures'])
                for member, child in declaration.get('members', {}).items():
                    require_metadata(child, path + '.' + member)
                for key in ('items', 'values'):
                    if key in declaration:
                        require_metadata(declaration[key], path + '.*')
            require_metadata(schema, name)
        for name in sorted(required):
            schema = definitions[name]
            for key in ('class', 'quantity', 'bound', 'entry', 'fixture'):
                self.assertTrue(schema.get(key), (name, key))
            local = dict(registry, roles={'fixture': schema})
            self.assertTrue(validate_packet('fixture', dict(never_declared=True), local))
            for member, declaration in schema['members'].items():
                if '$ref' in declaration:
                    declaration = definitions[declaration['$ref']]
                for key in ('class', 'quantity', 'bound', 'entry', 'fixture'):
                    self.assertTrue(declaration.get(key), (name, member, key))

    def test_k4_real_v7_reading_and_final_packets_use_the_native_closed_schema(self):
        from test_openness_reading_v7_turns import TurnPacketTests
        from research_harness import openness_reading_v7 as v7, review_route_capacity as capacity
        fixture = TurnPacketTests()
        fixture.setUp()
        fixture.packet['protocol'] = 'exactory-independent-review-v1'
        fixture.native['reviewer'] = 'reviewer-' + 'r' * 2000
        fixture.value['reviewer_id'] = fixture.native['reviewer']
        fixture.packet['reviewer_id'] = fixture.native['reviewer']
        packet, chain = fixture.prepare()
        registry = read_packet_registry()
        self.assertEqual(validate_packet('native_math', packet, registry), [])
        reference_registry = dict(registry, roles=dict(selection=registry['record_schemas']['carried_selection']))
        self.assertEqual(validate_packet('selection', chain['carried_selection'], reference_registry), [])
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        self.assertGreater(plan['frame']['reading']['fields']['bindings'], 2000)
        self.assertEqual(registry['definitions']['v7_reviewer']['bound'],
                         dict(serialized_characters='selected_request_input_limit'))
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        final = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'final'), 'final')
        self.assertEqual(validate_packet('native_math', final, registry), [])
        # Native protocol v1 also admits stored v5/v6 union members. Validate
        # the v7 binding itself against its version-specific closed declaration.
        for field, value, definition in (('reading_turn', packet, 'v7_reading_binding'),
                                         ('reading_final', final, 'v7_final_binding')):
            binding_registry = dict(registry, roles=dict(binding=registry['definitions'][definition]))
            self.assertEqual(validate_packet('binding', value[field], binding_registry), [])
            changed = copy.deepcopy(value[field])
            changed['capacity']['undeclared'] = True
            self.assertTrue(validate_packet('binding', changed, binding_registry))
        for field in ('reading_turn', 'reading_final'):
            value = copy.deepcopy(packet if field == 'reading_turn' else final)
            value[field]['capacity']['unreviewed'] = 1
            self.assertTrue(validate_packet('native_math', value, registry))

    def test_k4_final_gap_rows_keep_forecast_conditions_and_reviewer_text(self):
        from test_openness_reading_v7_turns import TurnPacketTests
        from research_harness import openness_reading_v7 as v7, review_route_capacity as capacity
        fixture = TurnPacketTests()
        fixture.setUp()
        fixture.packet['protocol'] = 'exactory-independent-review-v1'
        statement = fixture.native['scientific_context']['openness_minimum']['statement']
        fixture.native['scientific_context']['minimum_gaps'] = [dict(
            formulation=statement, condition='A synthetic unresolved condition.',
            outcomes=['supported_open'])]
        _, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        state['gaps'] = [dict(handle='h1', formulation=statement,
                             text='A synthetic reviewer gap.', status='verified')]
        final = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'final'), 'final')
        gaps = final['reading_final']['gaps']
        self.assertEqual(gaps[0]['condition'], 'A synthetic unresolved condition.')
        self.assertEqual(gaps[1]['text'], 'A synthetic reviewer gap.')
        registry = read_packet_registry()
        closed = dict(registry, roles=dict(binding=registry['definitions']['v7_final_binding']))
        self.assertEqual(validate_packet('native_math', final, registry), [])
        self.assertEqual(validate_packet('binding', final['reading_final'], closed), [])
        for row in gaps:
            row['undeclared_gap_member'] = True
            self.assertTrue(validate_packet('native_math', final, registry))
            self.assertTrue(validate_packet('binding', final['reading_final'], closed))
            del row['undeclared_gap_member']

    def test_k9_reading_constructions_leave_the_gap_table_only_with_executed_evidence(self):
        matrix = capacity_guard.read_matrix()
        rows = {row['id']: row for row in matrix['entries']}
        for identifier in ('A4', 'Q1', 'Q4', 'Q12'):
            with self.subTest(entry=identifier):
                row = rows[identifier]
                self.assertEqual(row['coverage'], 'constructed')
                self.assertTrue(row['evidence']['tests'])
                self.assertFalse(row['verification_required'])
                self.assertNotIn(identifier, capacity_guard.build_gap_table(matrix)['gaps'])
                self.assertTrue(any('v7' in test or 'carried' in test for test in row['evidence']['tests']))
                changed = copy.deepcopy(row)
                changed['evidence']['tests'] = []
                self.assertTrue(capacity_guard.validate_entry(changed))
        # 0.92.0 completes Q5, Q7 and Q8 (capacity-guard-3); their evidence keeps the executed v7 part.
        for identifier in ('Q5', 'Q7', 'Q8'):
            self.assertEqual(rows[identifier]['coverage'], 'constructed')
            self.assertTrue(any('v7' in test or 'unread' in test for test in rows[identifier]['evidence']['tests']))
        self.assertEqual(rows['A13']['coverage'], 'stop_before_call')
        self.assertIn('floor', rows['A13']['coverage_scope'])

    def test_k4_affected_note_index_and_large_unread_final_variants_are_closed(self):
        from test_openness_reading_v7_turns import TurnPacketTests
        from test_openness_unread_final import UnreadArtifactTests
        from research_harness import openness_reading_v7 as v7, review_route_capacity as capacity
        fixture = TurnPacketTests()
        fixture.setUp()
        fixture.packet['protocol'] = 'exactory-independent-review-v1'
        _, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        state['affected_notes'] = [dict(handle='note-%d' % number,
            origin=dict(chain='origin-%d' % number, protocol='exactory-openness-reading-v6',
                assignment_id='old-%d' % number, attempt_id='attempt-%d' % number),
            characters=200, delivery_characters=1000) for number in range(400)]
        packet = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 1,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading')
        self.assertGreater(packet['reading_turn']['affected_notes']['index']['omitted'], 0)
        self.assertEqual(validate_packet('native_math', packet, read_packet_registry()), [])
        other, _, _, _, unread_state, value = UnreadArtifactTests().build_fixture(units=150)
        self.assertGreater(len(other.read(unread_state['unread']['artifact'])), 30000)
        fixture.artifacts.bodies.update(other.bodies)
        state['unread'] = unread_state['unread']
        final = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'final'), 'final')
        self.assertEqual(validate_packet('native_math', final, read_packet_registry()), [])
        self.assertGreater(final['reading_final']['unread']['omitted'], 0)
        final['reading_final']['unread']['rows'][0]['unreviewed'] = True
        self.assertTrue(validate_packet('native_math', final, read_packet_registry()))

    def test_final_pages_gaps_name_each_stopped_shape_and_its_executed_answers(self):
        matrix = capacity_guard.read_matrix()
        shapes = matrix['reading_property_ends']
        for shape in ('targets_5000', 'targets_20000', 'gaps_2000'):
            self.assertEqual(shapes[shape]['closing_unit'], 'final-pages')
            self.assertEqual(shapes[shape]['end'], 'review_route_capacity_below_floor')
            self.assertTrue(shapes[shape]['tests'])
        self.assertEqual(shapes['v7_refused_v6_admitted']['end'], 'recorded_finding_v6')
        self.assertEqual(shapes['denser_final']['end'], 'export_then_recorded_finding')

    def test_ledger_marks_turn_integration_done_and_names_all_three_units(self):
        ledger = json.loads((ROOT / 'tests/incidents/ledger.json').read_text())
        row = next(row for row in ledger['incidents'] if row['id'] == 'v7-reading-inventory-and-carried-state')
        self.assertNotIn('later', row['expected'])
        final = next(row for row in ledger['incidents'] if row['id'] == 'v7-capacity-guard-57-turn-final')
        self.assertEqual(final['test_kind'], 'real_shape')
        for owner in ('test_openness_reading_v7_turns', 'test_openness_carried_integration',
                      'test_openness_reading_v7_verification'):
            self.assertTrue(any(test.startswith(owner + '.') for test in final['tests']))
