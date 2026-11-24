"""K4/K5 declarations, unreachable writes, helper boundaries and real builders."""
import ast
import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from ci import capacity_packet_inventory as inventory
from ci.capacity_packet_inventory import (generate_quantity_shapes, inspect_packet_sources,
    read_packet_registry, validate_packet, QUANTITIES, build_quantity_shape_packet, compute_ast_digest, validate_open_members, inspect_quantity_factories, read_quantity_allowance)

ROOT = Path(__file__).resolve().parents[1]
PARTS_FINAL_MESSAGE = 'Parts fixture did not reach its final'


def find_members(registry):
    """Map the identity of each growing member to its declaration."""
    return {role + '/' + protocol + ':' + '.'.join(path): member
            for role, protocol, schema in inventory.iter_packet_schemas(registry)
            for path, member in inventory._walk_declarations(schema, registry, ())}


def make_registry():
    return dict(modules=['builder'], builders={'builder.build_packet': {'parameters': ['packet']}},
        readers=[], stdlib_readers=['len', 'sorted', 'str', 'isinstance'],
        roles={'result': {'members': {'protocol': {}, 'role': {}, 'known': {},
            'nested': {'members': {'known': {}}}}}},
        source_members=['known', 'nested', 'role', 'protocol'], computed_sites={},
        fixtures={'variant': {'builder': 'builder.build_packet'}})


def make_growing():
    return dict(grows=True, quantity='notes', bound='open', entry='Q5', fixture='variant', sample='x')


class PacketInventoryTests(unittest.TestCase):
    def test_character_quantity_route_uses_only_its_own_keyed_capacity(self):
        from ci.capacity_packet_inventory import _read_quantity_scale
        from test_review_route_capacity import build_route
        from capacity_evidence_fixtures import build_capacity_runtime
        from research_harness.review_capacity_evidence import describe_capacity_key
        route = dict(build_route('openai_responses_v1', 'unlisted', output=1000),
                     runtime=build_capacity_runtime('openai_responses_v1'))
        records = dict(review_route=dict(route=route), review_route_probe=dict(probe=dict(
            id='probe', route_id='route', capacity=dict(input_tokens=151000),
            capacity_evidence=dict(key=describe_capacity_key(route), input_tokens=151000), usage={})))
        registry = dict(quantity_routes=dict(synthetic=route), quantity_evidence=dict(synthetic=records))
        canonical = dict(dimension='characters', route='synthetic', route_limit=150000)
        self.assertEqual(_read_quantity_scale(canonical, 'texts', registry), ('characters', 4500000))
        registry['quantity_routes']['synthetic'] = dict(route, endpoint='https://another.test/responses')
        with self.assertRaises(ValueError):
            _read_quantity_scale(canonical, 'texts', registry)

    def test_built_member_is_closed_at_each_depth(self):
        registry = make_registry()
        self.assertEqual(validate_packet('result', {'known': 1, 'nested': {'known': 2}}, registry), [])
        self.assertTrue(validate_packet('result', {'other': 1}, registry))
        self.assertTrue(validate_packet('result', {'nested': {'protocol': 1}}, registry))

    def test_unreachable_literal_write_needs_declaration_without_building(self):
        source = 'def build_packet(packet):\n    if False:\n        packet["surprise"] = []\n    return packet\n'
        registry = make_registry()
        self.assertTrue(inspect_packet_sources({'builder': source}, registry))
        registry['roles']['result']['members']['surprise'] = make_growing()
        self.assertEqual(inspect_packet_sources({'builder': source}, registry), [])

    def test_growing_declaration_requires_bound_entry_and_variant_fixture(self):
        for field in ('bound', 'entry', 'fixture', 'quantity'):
            registry = make_registry()
            member = make_growing()
            member.pop(field)
            registry['roles']['result']['members']['new'] = member
            with self.subTest(field=field):
                self.assertTrue(validate_packet('result', {'new': []}, registry))
                self.assertTrue(inspect_packet_sources({'builder': 'def build_packet(packet):\n    return packet\n'}, registry))
                with self.assertRaises(ValueError):
                    list(generate_quantity_shapes(registry))

    def test_new_declared_member_generates_its_own_scaled_shape(self):
        registry = make_registry()
        registry['roles']['result']['members']['new'] = make_growing()
        shapes = list(generate_quantity_shapes(registry))
        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0]['path'], ('new',))
        self.assertEqual(len(shapes[0]['value']), 10000)
        self.assertEqual(shapes[0]['fixture'], 'variant')
        namespace = {}
        exec('def build_packet(state, value):\n    packet = {}\n    if state:\n        packet["new"] = value\n    return packet\n', namespace)
        packet = namespace['build_packet'](True, shapes[0]['value'])
        self.assertEqual(len(packet['new']), 10000)
        self.assertEqual(validate_packet('result', packet, registry), [])

    def test_computed_argument_name_fails_in_unreachable_branch(self):
        source = 'def build_packet(packet, name):\n    if False:\n        packet[name + "_new"] = 1\n    return packet\n'
        self.assertTrue(inspect_packet_sources({'builder': source}, make_registry()))

    def test_constant_names_must_equal_the_source_collection(self):
        source = 'NAMES = ("known", "nested")\ndef build_packet(packet):\n    for name in NAMES:\n        packet[name] = []\n    return packet\n'
        registry = make_registry()
        registry['computed_sites']['builder.build_packet'] = [dict(form='names', names=['known', 'nested'])]
        self.assertEqual(inspect_packet_sources({'builder': source}, registry), [])
        registry['computed_sites']['builder.build_packet'][0]['names'].pop()
        self.assertTrue(inspect_packet_sources({'builder': source}, registry))
        registry['computed_sites']['builder.build_packet'][0]['names'] = ['known', 'nested', 'extra']
        self.assertTrue(inspect_packet_sources({'builder': source}, registry))

    def test_names_cannot_assert_an_underivable_name(self):
        registry = make_registry()
        registry['computed_sites']['builder.build_packet'] = [dict(form='names', names=['known'])]
        source = 'def build_packet(packet, name):\n    packet[name] = []\n    return packet\n'
        self.assertTrue(inspect_packet_sources({'builder': source}, registry))

    def test_digest_keyed_collection_requires_complete_keyed_declaration(self):
        source = 'def build_packet(packet, digest):\n    packet["nested"][digest] = []\n    return packet\n'
        registry = make_registry()
        self.assertTrue(inspect_packet_sources({'builder': source}, registry))
        schema = dict(make_growing(), values={'members': {'known': {}}})
        registry['computed_sites']['builder.build_packet'] = [dict(form='keyed', member='nested', schema=schema)]
        self.assertEqual(inspect_packet_sources({'builder': source}, registry), [])
        del registry['computed_sites']['builder.build_packet'][0]['schema']['entry']
        self.assertTrue(inspect_packet_sources({'builder': source}, registry))

    def test_unlisted_helper_rejects_direct_nested_and_alias_packets(self):
        for argument, alias in [('packet', ''), ('packet["nested"]', ''), ('second', '    second = packet\n')]:
            source = 'import helper\ndef build_packet(packet, name):\n' + alias + '    if False:\n        helper.mutate(' + argument + ', name)\n    return packet\n'
            helper = 'def mutate(value, name):\n    value[name] = []\n'
            with self.subTest(argument=argument):
                self.assertTrue(inspect_packet_sources({'builder': source, 'helper': helper}, make_registry()))

    def test_listed_helper_is_inspected_with_receiving_parameter(self):
        registry = make_registry()
        registry['modules'].append('helper')
        source = 'import helper\ndef build_packet(packet, name):\n    if False:\n        helper.mutate(packet, name)\n    return packet\n'
        helper = 'def mutate(value, name):\n    value[name] = []\n'
        self.assertTrue(inspect_packet_sources({'builder': source, 'helper': helper}, registry))
        registry['computed_sites']['helper.mutate'] = [dict(form='names', names=['known'])]
        helper = 'def mutate(value, name):\n    for key in ("known",):\n        value[key] = []\n'
        self.assertEqual(inspect_packet_sources({'builder': source, 'helper': helper}, registry), [])

    def test_registered_reader_cannot_mutate_parameter(self):
        registry = make_registry()
        registry['readers'] = ['helper.mutate']
        source = 'import helper\ndef build_packet(packet):\n    helper.mutate(packet)\n    return packet\n'
        helper = 'def mutate(value):\n    value["known"] = []\n'
        self.assertTrue(inspect_packet_sources({'builder': source, 'helper': helper}, registry))
        helper = 'def mutate(value):\n    return len(value)\n'
        self.assertEqual(inspect_packet_sources({'builder': source, 'helper': helper}, registry), [])

    def test_reader_cannot_forward_to_unlisted_helper(self):
        registry = make_registry()
        registry['readers'] = ['helper.read']
        source = 'import helper\ndef build_packet(packet):\n    return helper.read(packet)\n'
        helper = 'import outside\ndef read(value):\n    return outside.read(value)\n'
        self.assertTrue(inspect_packet_sources({'builder': source, 'helper': helper}, registry))

    def test_module_global_and_attribute_escape_fail(self):
        for statement in ('global saved\n    saved = packet', 'holder.saved = packet'):
            source = 'def build_packet(packet, holder):\n    ' + statement + '\n    return packet\n'
            with self.subTest(statement=statement):
                self.assertTrue(inspect_packet_sources({'builder': source}, make_registry()))

    def test_parameter_callee_fails(self):
        source = 'def build_packet(packet, callback):\n    callback(packet)\n    return packet\n'
        self.assertTrue(inspect_packet_sources({'builder': source}, make_registry()))

    def test_update_unpack_and_comprehension_fail_closed(self):
        for statement in ('packet.update(extra)', 'packet.setdefault(name, [])',
                          'packet = {**packet, **extra}', 'packet = {key: 1 for key in extra}'):
            source = 'def build_packet(packet, extra, name):\n    ' + statement + '\n    return packet\n'
            with self.subTest(statement=statement):
                self.assertTrue(inspect_packet_sources({'builder': source}, make_registry()))

    def test_unlisted_module_calling_a_packet_builder_fails(self):
        registry = make_registry()
        sources = {'builder': 'def build_packet(packet):\n    return packet\n',
                   'consumer': 'from builder import build_packet\ndef save():\n    return build_packet({})\n'}
        self.assertTrue(inspect_packet_sources(sources, registry))
        for consumer in ('def save():\n    return {"packet": {}}\n',
                         'def save(tx, value):\n    tx.put("review_assignment", "id", value)\n'):
            sources['consumer'] = consumer
            self.assertTrue(inspect_packet_sources(sources, registry))

    def test_unlisted_module_calling_a_builder_through_the_import_of_the_module_fails(self):
        from ci.capacity_packet_inventory import SOURCE_MISSING
        # Another function of the consumer imports the same name from elsewhere; that import binds only there.
        consumer = ('from builder import build_packet\n\n\ndef other():\n    from elsewhere import build_packet\n'
                    '    return build_packet()\n\n\ndef save():\n    return build_packet({})\n')
        sources = {'builder': 'def build_packet(packet):\n    return packet\n', 'consumer': consumer}
        self.assertIn(SOURCE_MISSING.format(module='consumer'), inspect_packet_sources(sources, make_registry()))

    def test_ast_certificates_keep_39_empty_fields_and_required_none(self):
        from ci.source_inventory import describe_source_ast
        function = ast.parse('def build_packet(packet):\n    return packet\n').body[0]
        baseline = compute_ast_digest(function)
        function.type_params = []
        if 'type_params' not in function._fields:
            function._fields = function._fields + ('type_params',)
        self.assertEqual(compute_ast_digest(function), baseline)
        self.assertEqual(describe_source_ast(ast.Constant(value=None)), 'Constant(value=None)')
        self.assertNotEqual(compute_ast_digest(ast.Constant(value=None)),
                            compute_ast_digest(ast.Constant(value='')))

    def test_shared_schema_ref_requires_growth_bound_entry_and_fixture(self):
        for missing in ('bound', 'entry', 'fixture'):
            registry = make_registry()
            declaration = make_growing()
            declaration.pop(missing)
            registry['definitions'] = {'shared': declaration}
            registry['roles']['result']['members']['known'] = {'$ref': 'shared'}
            with self.subTest(missing=missing):
                self.assertTrue(validate_packet('result', {'known': 'value'}, registry))
                self.assertTrue(inspect_packet_sources({'builder': 'def build_packet(packet):\n    return packet\n'}, registry))

    def test_quantity_vocabulary_is_fixed(self):
        self.assertEqual(len(QUANTITIES), 23)
        self.assertEqual(QUANTITIES[-3:], ('occurrences', 'citations', 'aliases'))


def build_staged_fixture_packets():
    from research_harness import review_protocol, review_staged, review_staged_protocol
    from test_review_staged_fixtures import epoch, task, full_card, observation
    for role in ('result', 'adjudicator'):
        focused = {'focused_scope': dict(review_pair_roots=['a' * 64, 'b' * 64], objection_id='objection-guard', objection_version='v1')} if role == 'adjudicator' else {}
        frozen = epoch(role=role, **focused)
        prior = task(frozen)
        card = full_card(frozen)
        observed = observation(frozen, prior, {'cards': [card]})
        for kind in review_staged.TASK_TYPES:
            assigned = task(frozen, kind, card_ids=[card['id']])
            yield role, kind, review_staged_protocol.build_packet(frozen, assigned, observations=[observed])


def build_parts_fixture_packets():
    from research_harness import review_protocol, review_parts
    from review_parts_fixtures import make_packet, observe, plan
    packet = make_packet(count=2, characters=20000)
    packet['protocol'] = review_protocol._PROBLEM_PROTOCOL
    observations = []
    for index in range(20):
        planned = plan(packet, observations, room=110000)
        yield {key: value for key, value in planned.items() if key != '_test_inventory'}
        if review_parts.find_kind(planned) == 'final':
            return
        observations.append(observe(planned))
    raise AssertionError(PARTS_FINAL_MESSAGE)


class QuantityFactoryTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_correction_evidence_union_closes_each_legal_locator_shape(self):
        from research_harness.strategy import evidence_fields
        from research_harness.source_links import validate_link_fields
        registry = read_packet_registry()
        artifact = dict(path='research/sources/objects/' + 'a' * 64,
                        sha256='a' * 64, size=1, media_type='text/plain')
        text = dict(kind='text', start=0, end=1, quote='x')
        locators = [text, dict(kind='span', start=0, end=1, sha256='b' * 64, excerpt='x'),
            dict(kind='json', pointer='/claim', value=dict(claim=[True, {'scope': 'finite'}])),
            dict(kind='pdf', page_index=0, printed_page='iv', region=[0, 0, 1, 1]),
            dict(kind='html', anchor=text, assets=[dict(url='https://example.invalid/figure.png',
                source_id='captured-asset', artifact=artifact)])]
        for locator in locators:
            # These are closed grammar shapes. The separate real constructor
            # test proves source/reading availability and exact-byte delivery.
            reference = dict(kind='source', link=dict(version_id='arxiv:2601.76001v1',
                source_id='capture', artifact=artifact, locator=locator))
            evidence_fields(reference, review_binding=True)
            validate_link_fields(reference['link'])
            packet = dict(protocol='exactory-reassessment-disposition-correction-v1',
                original_output=dict(reassessment=dict(findings=[dict(evidence=[reference])])))
            with self.subTest(locator=locator['kind']):
                self.assertEqual(validate_packet('correction', packet, registry,
                                                request_limit_characters=1000), [])
                if locator['kind'] == 'json':
                    self.assertTrue(validate_packet('correction', packet, registry))
                changed = copy.deepcopy(packet)
                changed['original_output']['reassessment']['findings'][0]['evidence'][0]['link']['locator']['unregistered'] = 'x'
                self.assertTrue(validate_packet('correction', changed, registry,
                                                request_limit_characters=1000))
        for reference in (artifact, dict(kind='record', record_kind='problem-value', id='value',
                                        digest='c' * 64, origin='d' * 64)):
            evidence_fields(reference, review_binding=True)
            packet = dict(protocol='exactory-reassessment-disposition-correction-v1',
                original_output=dict(reassessment=dict(findings=[dict(evidence=[reference])])))
            self.assertEqual(validate_packet('correction', packet, registry), [])

    def test_correction_record_and_source_references_use_real_eligible_builder(self):
        from capacity_packet_fixtures import build_scaled_correction_references
        from research_harness import review_output_correction
        original = review_output_correction.prepare_packet
        registry = read_packet_registry()
        for quantity in ('identifiers', 'sources'):
            observed = []
            def prepare(*args, **kwargs):
                packet = original(*args, **kwargs)
                observed.append(copy.deepcopy(packet))
                return packet
            with self.subTest(quantity=quantity), patch.object(
                    review_output_correction, 'prepare_packet', side_effect=prepare):
                packet = build_scaled_correction_references(quantity, 31)
                self.assertIn(packet, observed)
                self.assertEqual(validate_packet('correction', packet, registry), [])
                output = packet['original_output']
                arrays = [output['value']['evidence'], output['objections'][0]['evidence'],
                    output['value']['objection_findings'][0]['evidence'],
                    output['reassessment']['findings'][0]['evidence']]
                arrays.extend(row['evidence'] for row in output['value']['assurances'])
                for references in arrays:
                    self.assertEqual({row.get('kind', 'artifact') for row in references},
                                     {'artifact', 'record', 'source'})
                    if quantity == 'identifiers':
                        self.assertGreaterEqual(max(len(row.get('id', '')) for row in references), 31)
                        self.assertGreaterEqual(max(len(row.get('record_kind', '')) for row in references), 31)
                    else:
                        source = next(row['link'] for row in references if row.get('kind') == 'source')
                        self.assertGreaterEqual(len(source['locator']['quote']), 31)
                changed = copy.deepcopy(packet)
                changed['original_output']['reassessment']['findings'][0]['evidence'][0]['undeclared'] = 'x'
                self.assertTrue(validate_packet('correction', changed, registry))

    def test_correction_history_component_uses_frozen_reader_and_exact_copy(self):
        from unittest.mock import patch
        from capacity_packet_fixtures import build_scaled_correction_history
        from research_harness import review_context_repair, review_output_correction
        observed = []
        original = review_context_repair.frozen_reassessment_packet
        def read_frozen(records, artifacts, assignment):
            value = original(records, artifacts, assignment)
            self.assertEqual(value, json.loads(artifacts.read(assignment['packet'])))
            observed.append(value)
            return value
        with patch.object(review_context_repair, 'frozen_reassessment_packet', side_effect=read_frozen):
            packet = build_scaled_correction_history('reviews', 3)
        self.assertEqual(observed, [packet])
        self.assertEqual(len(packet['historical_reviews']), 3)
        self.assertEqual(len({row['id'] for row in packet['historical_reviews']}), 3)
        self.assertEqual(len({row['assignment_id'] for row in packet['historical_reviews']}), 3)
        scaled = build_scaled_correction_history('texts', 17)['historical_reviews'][0]
        self.assertEqual(scaled['payload']['value']['reason'], 'x' * 17)
        self.assertEqual(scaled['payload']['limitations'], ['x' * 17])
        self.assertEqual(validate_packet('correction', dict(
            protocol=review_output_correction.PROTOCOL,
            historical_reviews=packet['historical_reviews']), read_packet_registry()), [])

        # The production correction builder preserves this complete component;
        # eligibility and whole-request planning retain their separate Q11 tests.
        from test_review_output_correction import CorrectionTests
        case = CorrectionTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_refused()
        records, artifacts = case.fixture.read_records(), case.fixture.artifacts
        assignment = records['review_assignment'][case.case.assignment['id']]
        frozen = original(records, artifacts, assignment)
        correction = review_output_correction.prepare_packet(records, artifacts, assignment['id'])
        self.assertEqual(correction['historical_reviews'], frozen['historical_reviews'])

    def test_shipped_properties_execute_scaled_upstream_factories_and_gaps_are_explicit(self):
        from ci.capacity_guard import read_matrix
        from capacity_packet_fixtures import FACTORIES
        registry = read_packet_registry()
        self.assertEqual(inspect_quantity_factories(registry, read_matrix(), FACTORIES), [])
        self.assertTrue(any(value.get('canonical', {}).get('entry') == 'Q6'
                            for value in registry['quantity_contracts'].values()))

    def test_legacy_turns_use_the_v6_builder_and_other_role_frames_keep_their_owner(self):
        from capacity_packet_fixtures import build_scaled_v6_reading_records
        packet = build_scaled_v6_reading_records('turns', 8)
        record = packet['reading_turn']['reading_record']
        self.assertEqual(record['protocol'], 'exactory-openness-reading-v6')
        self.assertEqual(len(record['turns']), 8)
        self.assertEqual(len({(row['assignment_id'], row['attempt_id'])
                              for row in record['turns']}), 8)
        registry = read_packet_registry()
        self.assertEqual(validate_packet('native_math', packet, registry), [])
        contracts = registry['quantity_contracts']
        lineage = contracts['native_math/exactory-independent-review-v1:reading_turn.reading_record.turns']
        self.assertEqual(lineage['canonical']['factory'], 'v6_reading_records')
        # The frames of other roles were gaps of field-classes; that unit is installed, and each member now has
        # the bound of its own role: the field class, or the admission of a request that no chain serves.
        members = find_members(registry)
        for path, bound in (
                ('manuscript/:evidence.work', 'request_admission'),
                ('readiness/:inputs.branches', 'field_class'),
                ('native_math/exactory-independent-review-v1:native.contract', 'field_class'),
                ('result/exactory-problem-review-v2:context_repair.predecessor_binding.attempt_ids', 'field_class')):
            with self.subTest(property=path):
                self.assertNotIn(path, contracts)
                self.assertEqual(list(members[path]['bound']), [bound])
        self.assertEqual(registry['quantity_gaps']['A1_frame_metadata']['closing_unit'], 'field-classes')

    def test_missing_factory_and_merged_closing_unit_fail_before_execution(self):
        from ci.capacity_guard import read_matrix
        from capacity_packet_fixtures import FACTORIES
        registry = read_packet_registry()
        missing = copy.deepcopy(registry)
        missing['quantity_factories'].pop('canonical_record_units')
        self.assertTrue(inspect_quantity_factories(missing, read_matrix(), FACTORIES))
        hidden = copy.deepcopy(registry)
        identity = next(iter(hidden['shipped_quantity_properties']))
        hidden['quantity_contracts'][identity] = {'gap': 'A1_frame_metadata', 'declared_entry': 'Q3'}
        matrix = read_matrix()
        self.assertIn('field-classes', matrix['installed_units'])
        self.assertIn(inventory.QUANTITY_COVERAGE_MISSING.format(path=identity),
                      inspect_quantity_factories(hidden, matrix, FACTORIES))

    def test_new_bounded_member_requires_reachable_executable_variant_not_a_legacy_gap(self):
        from research_harness import review_packets
        registry = make_registry()
        member = dict(make_growing(), bound={'characters': 5000000})
        member['quantity_contract'] = {'canonical': dict(factory='new', path=['known'], quantity='texts', route='stage1')}
        registry['roles'] = {'result': {'members': {'known': member}}}
        registry['quantity_factories'] = {'new': {'builder': 'research_harness.review_packets.scrub'}}
        registry['fixtures']['variant']['builder'] = 'research_harness.review_packets.scrub'
        declared = read_packet_registry()
        registry['quantity_routes'] = {'stage1': declared['quantity_routes']['stage1_text_route']}
        registry['quantity_evidence'] = {'stage1': declared['quantity_evidence']['stage1_text_route']}
        matrix = {'entries': [dict(id='Q5', units={'construction': []})], 'installed_units': []}
        factory = lambda quantity, size: review_packets.scrub({'known': 'x' * size})
        self.assertEqual(inspect_quantity_factories(registry, matrix, {'new': factory}), [])
        unreachable = lambda quantity, size: review_packets.scrub({'other': 'x' * size})
        self.assertTrue(inspect_quantity_factories(registry, matrix, {'new': unreachable}))
        self.assertTrue(inspect_quantity_factories(registry, matrix, {}))
        member['quantity_contract'] = {'gap': dict(entry='A1', declared_entry='Q5',
            property='result/:known', property_kind='frame_metadata',
            closing_unit='field-classes', guard='capacity-guard-3')}
        matrix['entries'].append(dict(id='A1', units={'construction': ['field-classes']}))
        self.assertTrue(inspect_quantity_factories(registry, matrix, {'new': factory}))


class ProductionPacketInventoryTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_probe_and_correction_operation_packets_have_closed_wire_variants(self):
        registry = read_packet_registry()
        for name, packet in registry['operation_examples'].items():
            role = 'correction' if name == 'correction' else 'probe'
            self.assertEqual(validate_packet(role, packet, registry), [])
            changed = dict(packet, undeclared_operation_member='unexpected')
            self.assertTrue(validate_packet(role, changed, registry))
        visual = dict(registry['operation_examples']['probe_visual'], allowed_control='secret')
        self.assertTrue(validate_packet('probe', visual, registry))

    def test_current_production_sources_have_reviewed_closed_inventory(self):
        registry = read_packet_registry()
        sources = {'.'.join(path.relative_to(ROOT).with_suffix('').parts): path.read_text()
                   for path in (ROOT / 'research_harness').glob('*.py')}
        self.assertEqual(inspect_packet_sources(sources, registry), [])
        self.assertTrue({'research_harness.review_protocol', 'research_harness.review_parts',
                         'research_harness.review_packets'} <= set(registry['modules']))
        self.assertTrue(registry['callee_inventory'])

    def test_production_unexecuted_literal_computed_and_helper_mutations_fail(self):
        registry = read_packet_registry()
        sources = {module: (ROOT / (module.replace('.', '/') + '.py')).read_text()
                   for module in registry['modules']}
        module = 'research_harness.review_protocol'
        original = sources[module]
        for mutation in ('packet["never_declared"] = []', 'packet[name] = []',
                         'unlisted_helper.mutate(packet, name)', 'holder.packet = packet',
                         'callback(packet)'):
            sources[module] = original.replace("    canonical_prompt(role)\n",
                '    if False:\n        ' + mutation + "\n    canonical_prompt(role)\n", 1)
            with self.subTest(mutation=mutation):
                self.assertTrue(inspect_packet_sources(sources, registry))

    def test_production_open_members_are_pinned_and_shapes_cover_their_declarations(self):
        registry = read_packet_registry()
        shapes = generate_quantity_shapes(registry)
        self.assertTrue(shapes)
        actual = sorted({shape['role'] + '/' + shape['protocol'] + ':' + '.'.join(shape['path'])
                         for shape in shapes if shape['bound'] == 'open'})
        self.assertTrue(set(actual) <= set(registry['open_members']))

    def test_every_growing_variant_reaches_the_production_wire_builder(self):
        from research_harness import review_transport as transport
        from research_harness.review_route_capacity import measure_request
        from research_harness.errors import ResearchError
        registry = read_packet_registry()
        route = dict(adapter='openai_responses_v1', endpoint='https://example.invalid/v1/responses',
                     model='fixture', configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        reached = set()
        for shape in generate_quantity_shapes(registry):
            packet = build_quantity_shape_packet(shape, registry)
            try:
                request = transport.request_body(route, 'Return JSON.', packet, order=())
            except ResearchError as error:
                # These sparse projections deliberately omit sibling-bound source
                # digests. The real builder must refuse that incomplete contract.
                if shape['path'][:3] == ('native', 'scientific_context', 'openness_delivery'):
                    self.assertEqual(error.code, 'openness_source_delivery_mismatch')
                else:
                    self.assertEqual(shape['path'][:1], ('complete_source_delivery',))
                    self.assertEqual(error.code, 'review_complete_source_route')
                reached.add(shape['fixture'])
                continue
            delivered = json.loads(request['input'][-1]['content'][len(transport.PACKET_LABEL):])
            self.assertEqual(delivered, packet)
            self.assertGreaterEqual(measure_request(route, request)['text_characters'], 10000)
            reached.add(shape['fixture'])
        self.assertTrue(reached)
        self.assertTrue(reached <= set(registry['fixtures']))

    def test_parts_variants_use_real_planner_and_builder(self):
        registry = read_packet_registry()
        for packet in build_parts_fixture_packets():
            self.assertEqual(validate_packet('result', packet, registry), [])

    def test_generated_staged_text_scales_actual_record_input_and_packet_builder(self):
        from research_harness import review_staged_protocol as producer
        from research_harness.artifacts import ArtifactStore
        from research_harness.errors import ResearchError
        from capacity_packet_fixtures import build_scaled_staged_originals
        registry = read_packet_registry()
        allowance = read_quantity_allowance(registry, 'stage1_text_route')
        packets = build_scaled_staged_originals('texts', 30 * allowance)
        for packet in packets:
            self.assertEqual(validate_packet('result', packet, registry), [])
        self.assertGreaterEqual(sum(len(row['text']) for packet in packets
            for row in packet['staged_review']['originals']), 30 * allowance)

    def test_staged_roles_and_task_variants_use_real_builders(self):
        registry = read_packet_registry()
        for role, kind, packet in build_staged_fixture_packets():
            with self.subTest(role=role, kind=kind):
                self.assertEqual(validate_packet(role, packet, registry), [])

    def test_reviewed_production_member_passes_only_with_complete_declaration(self):
        from research_harness import review_protocol
        from test_review_continuation import ContinuationTests
        registry = read_packet_registry()
        sources = {module: (ROOT / (module.replace('.', '/') + '.py')).read_text()
                   for module in registry['modules']}
        module = 'research_harness.review_protocol'
        marker = "    packet = {'protocol': PROTOCOL, 'role': role, 'reviewer_id': reviewer_id, 'dossier_id': dossier_id}\n"
        sources[module] = sources[module].replace(marker, marker +
            "    if records.get('capacity_guard_variant'):\n        packet['capacity_guard_member'] = [str(note)[:1] for note in records['capacity_guard_variant'][:10000]]\n", 1)
        self.assertTrue(inspect_packet_sources(sources, registry))
        tree = ast.parse(sources[module])
        certificate = registry['certificates'][module]
        certificate['ast_sha256'] = compute_ast_digest(tree)
        certificate['functions']['build_packet'] = compute_ast_digest(next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build_packet'))
        role_schema = registry['roles']['bar']['protocols']['exactory-problem-review-v2']
        if '$ref' in role_schema:
            role_schema = copy.deepcopy(registry['definitions'][role_schema['$ref']])
            registry['roles']['bar']['protocols']['exactory-problem-review-v2'] = role_schema
        declaration = dict(grows=True, quantity='notes', bound={'entries': 10000}, entry='Q5',
                           fixture='reviewed-new-member', sample='x', items={'class':'fixed','bound':{'characters':1}}, **{'class': 'fixed'})
        role_schema['members']['capacity_guard_member'] = declaration
        registry['fixtures']['reviewed-new-member'] = dict(builder='research_harness.review_protocol.build_packet')
        self.assertEqual(inspect_packet_sources(sources, registry), [])
        for field in ('bound', 'entry', 'fixture'):
            incomplete = copy.deepcopy(registry)
            incomplete['roles']['bar']['protocols']['exactory-problem-review-v2']['members']['capacity_guard_member'].pop(field)
            self.assertTrue(inspect_packet_sources(sources, incomplete))
        # Execute the reviewed production builder copy with the generated variant.
        case = ContinuationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        namespace = dict(review_protocol.__dict__)
        exec(compile(sources[module], '<reviewed-production-builder>', 'exec'), namespace)
        records = dict(case.case.read_records(), capacity_guard_variant=['x'] * 10000)
        packet = namespace['build_packet'](records, case.case.artifacts, None, 'bar', 'first')
        self.assertEqual(validate_packet('bar', packet, registry), [])
        shape = next(shape for shape in generate_quantity_shapes(registry)
                     if shape['fixture'] == 'reviewed-new-member')
        self.assertEqual(packet['capacity_guard_member'], shape['value'])
        oversized = namespace['build_packet'](dict(records, capacity_guard_variant=['long note'] * 10001),
            case.case.artifacts, None, 'bar', 'first')
        self.assertEqual(len(oversized['capacity_guard_member']), 10000)
        self.assertTrue(all(len(note) <= 1 for note in oversized['capacity_guard_member']))
        declaration['quantity_contract'] = {'canonical': dict(factory='reviewed-new-member',
            path=['capacity_guard_member'], quantity='notes', dimension='entries')}
        focused = copy.deepcopy(registry)
        focused['roles'] = {'bar': {'protocols': {'exactory-problem-review-v2': {
            'members': {'capacity_guard_member': declaration}}}}}
        focused['certificates'] = {}
        focused['shipped_quantity_properties'] = []
        focused['quantity_factories'] = {'reviewed-new-member': {'builder': 'research_harness.review_protocol.build_packet'}}
        def build_scaled_variant(quantity, size):
            upstream = dict(case.case.read_records(), capacity_guard_variant=['x'] * size)
            return review_protocol.build_packet(upstream, case.case.artifacts, None, 'bar', 'first')
        with patch.object(review_protocol, 'build_packet', namespace['build_packet']):
            self.assertEqual(inspect_quantity_factories(focused, {'entries': [dict(id='Q5', units={'construction': []})], 'installed_units': []},
                {'reviewed-new-member': build_scaled_variant}), [])

    def test_real_repair_and_reading_emissions_have_closed_schemas(self):
        import importlib
        from research_harness.artifacts import ArtifactStore
        registry = read_packet_registry()
        original = ArtifactStore.put
        repairs = set()
        readings = set()
        def validate_put(artifacts, data, media_type):
            if media_type == 'application/json':
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    # A segmented scientific JSON body is not a packet object.
                    return original(artifacts, data, media_type)
                if (isinstance(value, dict) and value.get('role') in registry['roles']
                        and 'protocol' in value and 'evidence' in value):
                    self.assertEqual(validate_packet(value['role'], value, registry), [])
                    if 'context_repair' in value:
                        repairs.add(value['role'])
                    if 'reading_turn' in value:
                        readings.add(value['role'])
                    if value['role'] == 'native_math':
                        self.assertNotIn('context_repair', value)
            return original(artifacts, data, media_type)
        methods = (
            ('test_review_parts_repair_journey', 'ResultRepairJourneyTests', 'test_contaminated_v2_intermediate_turns_are_scientific_history'),
            ('test_review_parts_repair_journey', 'AdjudicatorRepairJourneyTests', 'test_registered_adjudicator_can_continue_a_repair_with_ordinary_base_prompt'),
            ('test_review_parts_repair_journey', 'BarRepairJourneyTests', 'test_fitting_bar_repair_stays_ordinary'),
            ('test_research_context_repair_integration', 'ContextRepairIntegrationTests', 'test_completed_slate_can_repair_actual_contamination_in_its_inherited_bar_context'),
            ('test_d2_outcomes', 'OutcomeTests', 'test_native_math_handoff_accepts_a_new_attested_packet'),
            ('test_review_parts_native', 'NativeOpennessRoutingTests', 'test_oversized_openness_minimum_keeps_the_reading_chain'))
        with patch.object(ArtifactStore, 'put', validate_put):
            for module, class_name, method in methods:
                case = getattr(importlib.import_module(module), class_name)(method)
                with self.subTest(fixture=class_name + '.' + method):
                    try:
                        case.setUp()
                        getattr(case, method)()
                    finally:
                        case.doCleanups()
        self.assertEqual(repairs, {'bar', 'slate', 'result', 'adjudicator'})
        self.assertEqual(readings, {'native_math'})

    def test_initial_open_allowance_is_immutable_and_actual_opens_can_shrink(self):
        registry = read_packet_registry()
        expanded = copy.deepcopy(registry)
        expanded['open_members'].append('result/new-protocol:new-open-member')
        self.assertTrue(validate_open_members(expanded))
        with self.assertRaises(ValueError):
            next(generate_quantity_shapes(expanded))
        reduced = copy.deepcopy(registry)
        member = next(value for value in reduced['definitions'].values()
                      if value.get('grows') and value.get('bound') == 'open')
        member['bound'] = {'characters': 10000}
        member['class'] = 'fixed'
        self.assertEqual(validate_open_members(reduced), [])
        self.assertEqual(reduced['open_members'], registry['open_members'])

    def test_k4_only_the_record_copy_members_keep_the_bound_open(self):
        registry = read_packet_registry()
        self.assertEqual(inventory.RECORD_COPY_OPEN_PREFIXES, ())
        self.assertEqual(inventory.list_open_members(registry), set())
        self.assertEqual(validate_open_members(registry), [])
        bounds = {}
        for member in find_members(registry).values():
            kind = next(iter(member['bound']))
            bounds[kind] = bounds.get(kind, 0) + 1
        self.assertEqual(bounds, dict(field_class=6683, request_admission=1428, chain_stop=21,
                                      serialized_characters=35))
        # A copy of the registry with one more open member fails, and so does a packet checked against it.
        changed = copy.deepcopy(registry)
        identity = 'bar/exactory-problem-review-v2:pending_source_requests'
        member = find_members(changed)[identity]
        member.update(bound='open', **{'class': 'open'})
        self.assertIn(inventory.OPEN_MEMBER_PRESENT.format(path=identity), validate_open_members(changed))
        self.assertTrue(validate_packet('bar', dict(protocol='exactory-problem-review-v2'), changed))

    def test_k5_every_bounded_member_executes_its_field_class_or_its_admission_stop(self):
        """Each of the 8,132 bounded members is classified by the frame builder one character above its size.

        Assertion K5 executes every declared member, so the count is that of the registry, and the size is the
        smallest at which the frame moves the member. Measured in the must-pass run: 8.7 s on 3.12, 11.5 s on 3.9."""
        from ci.capacity_guard import read_matrix
        self.assertEqual(inventory.inspect_member_bounds(read_packet_registry(), read_matrix()), [])

    def test_k5_a_changed_bound_a_missing_stop_or_a_framed_overflow_fails(self):
        from ci.capacity_guard import read_matrix
        from research_harness import review_field_classes
        registry = read_packet_registry()
        matrix = read_matrix()
        def focus(role, protocol):
            # Shared definitions are copied too, so that a change of one copy reaches no other.
            return copy.deepcopy(dict(registry, certificates={}, roles={role: dict(protocols={
                protocol: registry['roles'][role]['protocols'][protocol]})}))
        bar = focus('bar', 'exactory-problem-review-v2')
        self.assertEqual(inventory.inspect_member_bounds(bar, matrix), [])
        identity = 'bar/exactory-problem-review-v2:pending_source_requests'
        find_members(bar)[identity]['bound']['field_class']['characters'] += 1
        self.assertIn(inventory.MEMBER_BOUND_INVALID.format(path=identity), inventory.inspect_member_bounds(bar, matrix))
        standalone = focus('standalone', 'exactory-independent-review-v1')
        self.assertEqual(inventory.inspect_member_bounds(standalone, matrix), [])
        identity, member = next(iter(find_members(standalone).items()))
        member['bound']['request_admission']['stop'] = 'review_output_over_cap_without_tests'
        self.assertIn(inventory.MEMBER_BOUND_INVALID.format(path=identity),
                      inventory.inspect_member_bounds(standalone, matrix))
        original = review_field_classes.classify_packet
        def keep_in_frame(route, role, packet, **kwargs):
            classified = original(route, role, packet, **kwargs)
            for row in classified['fields']:
                row['travel'] = 'frame'
            return classified
        with patch.object(review_field_classes, 'classify_packet', keep_in_frame):
            errors = inventory.inspect_member_bounds(focus('bar', 'exactory-problem-review-v2'), matrix)
        self.assertIn(inventory.MEMBER_FRAME_UNBOUNDED.format(
            path='bar/exactory-problem-review-v2:pending_source_requests'), errors)

    def test_real_problem_bar_packet_has_no_undeclared_member(self):
        from test_review_continuation import ContinuationTests
        case = ContinuationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        packet = json.loads(case.case.artifacts.read(case.assignment['packet']))
        self.assertEqual(validate_packet('bar', packet, read_packet_registry()), [])
        from research_harness import bar_sources
        self.assertEqual(packet['source_rules'], bar_sources.PROTOCOL_V3)
        packet['subject']['role'] = 'known-name-in-wrong-place'
        self.assertTrue(validate_packet('bar', packet, read_packet_registry()))
