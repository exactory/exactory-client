"""Role frames, overflow units and bounded parts reference rows."""
import copy
import importlib
import json
import tempfile
from pathlib import Path
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from research_harness import review_protocol, review_route_capacity as capacity
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_route_capacity import build_route


def build_problem_packets():
    from review_parts_fixtures import PartsResultCase
    case = PartsResultCase()
    case.setUp()
    try:
        fixture = case.case
        records = fixture.read_records()
        return {role: review_protocol.build_packet(records, fixture.artifacts, None, role, 'first')
                for role in ('bar', 'slate', 'result')}
    finally:
        case.doCleanups()


def build_imported_slate(characters):
    from research_harness import bar_sources, problem_forming
    from test_problem_forming_import import FormingImportTests
    from test_research_problem_forming import build_forming
    from test_research_problem_slate import ProblemSlateTests
    source = FormingImportTests()
    source.setUp()
    case = ProblemSlateTests()
    case.setUp()
    try:
        artifact = source.source.artifacts.put(b'x' * characters, 'text/plain')
        prompt = source.source.artifacts.put(b'Explore a finite comparison.', 'text/plain')
        call = source.source.mutate(problem_forming.record_forming, build_forming('large-call', forms=[],
            performer=dict(kind='model_lane', lane_id='forming-lane', model='fixture-model', effort='reported',
                           prompt=prompt, output=artifact)))['result']
        imported = source.import_history(destination=case)
        reference = source.reference('forming_call', call, imported['source_origin'])
        case.record_candidate(source=dict(kind='computation', reference='An imported observation.', evidence=[reference]))
        case.observe_bars()
        # Rebuild the historical packet form that carried the import's bodies.
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            return review_protocol.build_packet(case.read_records(), case.artifacts, None, 'slate', 'first')
    finally:
        case.doCleanups()
        source.doCleanups()


def build_remaining_packets():
    from research_harness import review_packets, verification
    from research_harness.artifacts import ArtifactStore
    from capacity_packet_fixtures import build_scaled_stage2_native_records
    from test_capacity_packet_inventory import build_staged_fixture_packets, build_parts_fixture_packets
    from test_research_review_protocol import ReviewProtocolTests
    from test_research_verification import ResearchVerificationTests
    packets = []
    with tempfile.TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        reference = artifacts.put(b'A finite manuscript.', 'text/plain')
        bundle = dict(id='paper', digest='a' * 64, files=dict(pdf=dict(artifact=reference)),
                      claim_evidence=[], execution_observations={}, review_inputs=dict(synthesis=dict(
                          sections=dict(standards=dict(payload={'field': 'A finite standard.'})), foundation={})))
        records = dict(publication_bundle=dict(paper=bundle), collection=dict(cohort=dict(id='cohort',
            definition=dict(corpus='arxiv', primaryCategory='math.CO', windowStart='2026-01-01', windowEnd='2026-01-31'))),
            literature_scope=dict(research=dict(collection_ids=['cohort'])))
        packets.append(('manuscript', 'manuscript', review_protocol.build_packet(
            records, artifacts, None, 'manuscript', 'reviewer', bundle=bundle)))
        packets.append(('standalone', 'standalone', review_protocol.build_packet(
            {}, artifacts, None, 'standalone', 'reviewer', artifact_refs=[reference])))
        packets.append(('neutral_manuscript', 'manuscript', review_packets.manuscript_packet(records, bundle, artifacts)))
        packets.append(('readiness', 'readiness', review_packets.readiness_packet(dict(review_inputs=dict(
            candidate=dict(statement='A finite claim.'), synthesis=dict(sections={}, foundation={})), execution_observations={}))))
        packets.append(('round', 'round', review_packets.round_packet(records, bundle,
            dict(payload={}, digest='b' * 64, closes=1), artifacts)))
    case = ReviewProtocolTests()
    case.setUp()
    try:
        case.bar_reviews()
        for role in ('bar', 'slate', 'adjudicator'):
            context = dict(review_ids=['review-first', 'review-second'], objection_id='objection-first') if role == 'adjudicator' else {}
            packets.append(('study_' + role, role, review_protocol.build_packet(case.records(), case.artifacts,
                'dossier', role, 'third' if role == 'adjudicator' else 'first', **context)))
    finally:
        case.doCleanups()
    case = ResearchVerificationTests()
    case.setUp()
    try:
        task = case.mutate(verification.record_task, dict(task=case.task))['result']
        packets.append(('verification', 'verification', review_protocol.build_packet(case.store.snapshot()['records'],
            case.artifacts, None, 'verification', 'reviewer', verification_task_id=task['digest'])))
    finally:
        case.doCleanups()
    native = build_scaled_stage2_native_records('sources', 2)
    packets.extend([('native_math', 'native_math', native), ('native_ingress', 'native_math', native['native'])])
    for role, kind, packet in build_staged_fixture_packets():
        packets.append(('staged_' + role + '_' + kind, role, packet))
    for index, packet in enumerate(build_parts_fixture_packets()):
        packets.append(('parts_' + str(index), 'result', packet))
    return packets


def build_stored_packet_digest(packet):
    from research_harness.artifacts import ArtifactStore
    with tempfile.TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        return review_protocol._put_json(artifacts, packet)['sha256']


def walk_fields(value, path=()):
    if isinstance(value, dict):
        for key in sorted(value):
            yield path + (key,)
            yield from walk_fields(value[key], path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield path + (index,)
            yield from walk_fields(item, path + (index,))


class FieldClassTests(unittest.TestCase):
    def setUp(self):
        self.fields = importlib.import_module('research_harness.review_field_classes')
        self.route = build_route()

    def test_every_role_and_closed_packet_form_has_rows_at_every_depth(self):
        from ci.capacity_packet_inventory import read_packet_registry, generate_quantity_shapes, build_quantity_shape_packet
        registry = read_packet_registry()
        self.assertTrue(set(review_protocol.ROLES) <= set(self.fields.FIELD_CLASSES))
        # Use the registry's real wire forms with small upstream values here;
        # stress bodies are exercised separately through canonical factories.
        reached = set()
        for shape in generate_quantity_shapes(registry):
            if shape['role'] == 'probe':
                continue
            value = shape['value']
            if isinstance(value, list):
                shape['value'] = value[:1]
            elif isinstance(value, dict):
                key = next(iter(value))
                shape['value'] = {key: value[key]}
            else:
                shape['value'] = 'x'
            packet = build_quantity_shape_packet(shape, registry)
            classified = self.fields.classify_packet(self.route, shape['role'], packet)
            self.assertEqual({tuple(row['path']) for row in classified['fields']}, set(walk_fields(packet)))
            self.assertTrue(all(row['row']['class'] in ('fixed', 'collection', 'body', 'carried', 'reference')
                                for row in classified['fields']))
            reached.add(shape['role'])
        self.assertTrue(set(review_protocol.ROLES) <= reached)

    def test_real_problem_and_canonical_native_builders_classify(self):
        from capacity_packet_fixtures import build_scaled_record_units, build_scaled_stage2_native_records
        packets = build_problem_packets()
        for role, packet in packets.items():
            classified = self.fields.classify_packet(self.route, role, packet)
            self.assertEqual(set(walk_fields(packet)), {tuple(row['path']) for row in classified['fields']})
        for packet in (build_scaled_record_units('texts', 128), build_scaled_stage2_native_records('sources', 2)):
            self.fields.classify_packet(self.route, packet['role'], packet)

    def test_source_continuation_builders_declare_every_field(self):
        from research_harness import (bar_sources, capture_selection, problem_claims, review_context_repair,
                                      review_prompt_paragraphs)
        from research_harness.artifacts import ArtifactStore
        from test_review_prompt_paragraphs import build_packet as build_paragraph_source
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            selection = dict(version_id='work', source_id='capture', original_sha256='a' * 64,
                             text_sha256='b' * 64, protocol=capture_selection.PROTOCOL)
            for problem in (False, True):
                records = {capture_selection.SELECTION_KIND: {'capture': selection}}
                if problem:
                    records['workspace'] = {'problem': {'area': 'finite', 'field': 'finite'}}
                predecessor = dict(source_inventory=bar_sources.inventory(records, artifacts), pending_source_requests=[],
                    bar_ids=[], candidates=[], candidate_set_digest=digest([]),
                    context_repair=dict(assignment_id='predecessor', slot_id='slot'))
                for rules in (bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3):
                    for role in bar_sources.ROLES:
                        with self.subTest(problem=problem, role=role, rules=rules), ExitStack() as stack:
                            previous = dict(predecessor)
                            if problem and role == 'result':
                                previous.update(build_paragraph_source(accounts=True),
                                    problem_evidence_digest=problem_claims.compute_problem_evidence_digest(records, artifacts))
                            if rules == bar_sources.PROTOCOL_V3:
                                previous.update(source_rules=rules, settled_source_requests=[
                                    dict(request=dict(id='held'), limitations=['Uncaptured.'])])
                            for module, name, result in (
                                    (bar_sources, 'SESSION_PROTOCOL', rules),
                                    (review_protocol, '_source_parent', (dict(id='prior',
                                        packet_digest=digest(previous)), {'output': {}})),
                                    (review_protocol, 'source_delivery_packet', previous),
                                    (review_protocol, '_find_session_bodies', {}),
                                    (review_protocol, 'add_direction_evidence', None),
                                    (review_protocol, 'add_problem_bars', []),
                                    (review_protocol, 'require_calibrated_bars', None),
                                    (review_context_repair, 'collect_problem_review_history', [])):
                                target = patch.object(module, name, result) if name == 'SESSION_PROTOCOL' else (
                                    patch.object(module, name, return_value=result))
                                stack.enter_context(target)
                            packet = review_protocol.build_packet(records, artifacts, None if problem else 'dossier',
                                                                  role, 'reviewer', prior_assignment_id='prior')
                            if problem and role == 'result':
                                self.assertIn('accounts', packet[review_prompt_paragraphs.CONDITIONS])
                                self.assertEqual(packet['history_delivery'], review_protocol.transport.HISTORY_COMPACT_V2)
                            _, table = self.fields._select_table(role, packet)
                            self.assertEqual(packet.keys() - table.keys(), set())
                            self.fields.classify_packet(self.route, role, packet)

    def test_repair_builders_declare_raw_and_normalized_history(self):
        from research_harness import bar_sources, review_parts_repair, review_supplied_evidence
        from research_harness.artifacts import ArtifactStore
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            for problem in (False, True):
                for role in (*bar_sources.ROLES, 'adjudicator'):
                    for rules in (bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3):
                        if role == 'adjudicator' and rules == bar_sources.PROTOCOL_V3:
                            continue
                        with self.subTest(problem=problem, role=role, rules=rules), ExitStack() as stack:
                            form = (review_protocol._PROBLEM_PROTOCOL if problem else
                                    review_supplied_evidence.PROTOCOL if role == 'adjudicator' else bar_sources.PROTOCOL)
                            records = {'workspace': {'problem': {'area': 'A', 'field': 'F'}}} if problem else {}
                            previous = dict(protocol=form, role=role, evidence=[])
                            if rules == bar_sources.PROTOCOL_V3:
                                previous['source_rules'] = rules
                            repair = dict(packet=previous, scientific_history=[
                                dict(assignment_id='prior', packet=previous, observed_output={})],
                                predecessor=dict(id='prior'), slot_id='slot', event_ids=[], probe_id='probe',
                                cause='a context event', historical_reviews=[])
                            value = dict(role=role, reviewer_id='new', dossier_id=None if problem else 'dossier')
                            if problem:
                                value['subject'] = dict(kind='problem', statement_id=None)
                            stack.enter_context(patch.object(bar_sources, 'describe_settlements', return_value=[
                                dict(request=dict(id='held'), limitations=['Uncaptured.'])]))
                            for name, result in (('add_problem_bars', []), ('add_problem_evidence', None),
                                    ('add_problem_objections', None), ('compute_problem_review_currency', digest([])),
                                    ('collect_historical_bar_objections', []), ('add_direction_evidence', None),
                                    ('_find_session_bodies', {})):
                                stack.enter_context(patch.object(review_protocol, name, return_value=result))
                            packet = review_protocol._repair_packet(records, artifacts, value, repair)
                            self.assertEqual(packet['protocol'], form)
                            self.assertIn('previously_supplied_evidence', packet)
                            self.fields.classify_packet(self.route, role, packet)
                            normalized = review_parts_repair.prepare_history(records, artifacts, packet, history=[])
                            self.fields.classify_packet(self.route, role, normalized)

    def test_registered_stored_role_members_have_field_classes(self):
        from ci.capacity_packet_inventory import iter_packet_schemas, read_packet_registry
        for role, form, schema in iter_packet_schemas(read_packet_registry()):
            if form in self.fields.FIELD_CLASSES.get(role, {}):
                with self.subTest(role=role, form=form):
                    self.assertEqual(schema.get('members', {}).keys() - self.fields.FIELD_CLASSES[role][form].keys(),
                                     set())

    def test_new_plans_reserve_exact_identity_and_old_bounds_replay(self):
        packet = dict(protocol='exactory-problem-review-v2', role='result', dossier_id=None,
                      subject=dict(kind='problem', statement_id='s'), reviewer_id='r' * 2000)
        frames = self.fields.describe_role_frame(self.route, 'result', packet, ('request',))
        profile = capacity.read_output_profile({}, self.route)
        plan = capacity.plan_chain_capacity(self.route, profile, frames=frames, inventory=[], targets=[])
        bounds = plan['frame']['request']['bounded_fields']
        projected = self.fields.classify_packet(self.route, 'result', packet, bounded_fields=bounds)
        self.assertEqual(projected['frame'], packet)
        self.assertEqual(projected['units'], [])
        tokens = capacity.describe_route_capacity(self.route, profile)['input_tokens']['value']
        minimum = (sum(frames['request'].values()) + capacity.describe_material_levels('request')[-1]
                   + capacity.NOTES_MAXIMUM_CHARACTERS + capacity.measure_capacity_value(self.route, []))
        from research_harness.review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
        minimum += BOUNDED_NOTICE_CHARACTERS
        measure = dict(rule='calibrated', characters_per_token=(minimum - 0.5) / tokens)
        with self.assertRaises(ResearchError) as raised:
            capacity.plan_chain_capacity(self.route, profile, frames=frames, inventory=[], targets=[], measure=measure)
        self.assertEqual(raised.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(raised.exception.details['minimum'], minimum)
        old_bounds = {'reviewer_id': 200, '$unit:reviewer_id': 0}
        old = self.fields.classify_packet(self.route, 'result', packet, bounded_fields=old_bounds)
        self.assertEqual(old['frame']['reviewer_id']['sha256'], digest(packet['reviewer_id']))
        self.assertEqual(old['units'][0]['sha256'], digest(packet['reviewer_id']))
        self.assertEqual(self.fields.classify_packet(
            self.route, 'result', packet, bounded_fields=dict(old_bounds, **{'$binding': 0})), old)

    def test_stored_parts_frame_keeps_its_members_after_binding_table_changes(self):
        from research_harness import bar_sources, review_parts_v3 as parts
        from research_harness.artifacts import ArtifactStore
        from research_harness.problem_openness import serialize_record

        packet = dict(protocol=bar_sources.PROTOCOL, role='result', dossier_id='dossier', reviewer_id='r' * 520,
                      assessor=dict(id='r' * 520, kind='agent'), dossier={}, evidence=[],
                      readiness=dict(kind='readiness', inputs={}),
                      support_contract=dict(candidate_digest='c' * 64, scope_note='s' * 40,
                                            evidence=[dict(reference='r' * 4100)]))
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            admission = parts.admit_packet({}, artifacts, self.route, packet, 'study_readiness')
            self.assertEqual(admission['state'], 'accept', admission.get('details'))
            plan, inventory = admission['plan'], admission['inventory']
            stored = dict(capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
                         artifact=artifacts.put(serialize_record(plan), 'application/json')))
            before = parts._build_frame(self.route, inventory, plan, 'final')
            self.assertEqual(before['support_contract']['candidate_digest'], 'c' * 64)
            original_bounds = plan['frame']['final']['bounded_fields']
            original_units = self.fields.list_frame_units(self.route, 'result', inventory['frame_source'], original_bounds)
            self.assertIn('$binding-paths:support_contract', original_bounds)
            for added in ('scope_note', 'evidence'):
                table = copy.deepcopy(self.fields.ROLE_BINDING_PATHS)
                table['result']['support_contract'] |= {added}
                with self.subTest(added=added), patch.object(self.fields, 'ROLE_BINDING_PATHS', table):
                    replay = capacity.read_chain_capacity_plan(artifacts, stored)
                    after = parts._build_frame(self.route, inventory, replay, 'final')
                    self.assertEqual(serialize_record(after), serialize_record(before))
                    self.assertEqual(self.fields.list_frame_units(self.route, 'result', inventory['frame_source'],
                                      replay['frame']['final']['bounded_fields']), original_units)

    def test_stored_binding_wrapper_keeps_its_nested_field_projection(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.problem_openness import serialize_record

        role = 'native_math'
        form = review_protocol.PROTOCOL
        packet = dict(protocol=form, role=role, reviewer_id='reviewer',
                      native=dict(kind='result', subject='s' * 17000, scientific_context=dict(
                                  minimum_gaps=['gap'], openness_minimum=dict(scope='finite'))))
        frames = self.fields.describe_role_frame(self.route, role, packet, ('request',))
        self.assertIn('$binding-paths:native', frames['request'])
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            declaration = dict(kind='request', frames=frames, inventory=[], targets=[])
            stored = capacity.prepare_assignment_capacity({}, artifacts, self.route, declaration)
            before = self.fields.classify_packet(self.route, role, packet, bounded_fields=frames['request'])
            self.assertEqual(before['frame']['native']['scientific_context']['minimum_gaps']['entries'], 1)
            with patch.dict(self.fields.FIELD_CLASSES[role][form], {
                    'native': dict(self.fields.FIELD_CLASSES[role][form]['native'], characters=1),
                    'native.scientific_context.minimum_gaps': {'class': 'fixed', 'characters': 0}}):
                replay = capacity.read_chain_capacity_plan(artifacts, stored)
                after = self.fields.classify_packet(self.route, role, packet,
                    bounded_fields=replay['frame']['request']['bounded_fields'])
            self.assertEqual(serialize_record(after['frame']), serialize_record(before['frame']))
            self.assertEqual(after['units'], before['units'])

    def test_reference_digests_stay_in_the_frame_without_binding_declarations(self):
        digests = ('candidate_set_digest', 'problem_evidence_digest', 'problem_review_currency')
        for role, forms in self.fields.FIELD_CLASSES.items():
            self.assertFalse(set(digests) & self.fields._find_binding_paths(role).keys())
            for form, table in forms.items():
                packet = {name: 'a' * 64 for name in digests if name in table}
                if packet:
                    packet['protocol'] = form
                    classified = self.fields.classify_packet(self.route, role, packet)
                    self.assertEqual(classified['frame'], packet)
                    self.assertEqual(classified['units'], [])

    def test_all_real_logical_neutral_native_staged_and_parts_forms_keep_base_bytes(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/review_field_class_packets.json').read_text())
        reached = set()
        for label, role, packet in build_remaining_packets():
            with self.subTest(form=label):
                self.assertEqual(digest(packet), expected[label]['value_sha256'])
                self.assertEqual(build_stored_packet_digest(packet), expected[label]['packet_sha256'])
                classified = self.fields.classify_packet(self.route, role, packet)
                self.assertEqual({tuple(row['path']) for row in classified['fields']}, set(walk_fields(packet)))
                self.assertEqual(digest(packet), expected[label]['value_sha256'])
                self.assertEqual(build_stored_packet_digest(packet), expected[label]['packet_sha256'])
                reached.add(role)
        self.assertTrue((set(review_protocol.ROLES) - {'result'}) <= reached)

    def test_unknown_roles_and_packet_forms_name_the_missing_declaration(self):
        for role, packet, missing in (('new_role', {}, 'new_role'),
                                      ('bar', {'protocol': 'new_protocol'}, 'new_protocol')):
            with self.assertRaises(ResearchError) as raised:
                self.fields.classify_packet(self.route, role, packet)
            self.assertEqual(raised.exception.code, 'review_input_unreadable')
            self.assertIn(missing, raised.exception.details.values())

    def test_builder_copy_with_added_field_names_the_unclassified_field(self):
        original = build_problem_packets()['bar']
        def build_changed():
            return dict(copy.deepcopy(original), undeclared_field='new')
        with self.assertRaises(ResearchError) as raised:
            self.fields.classify_packet(self.route, 'bar', build_changed())
        self.assertEqual(raised.exception.code, 'review_input_unreadable')
        self.assertEqual(raised.exception.details['field'], 'undeclared_field')

    def test_fixed_value_at_bound_stays_and_one_more_becomes_a_unit(self):
        rule = self.fields.FIELD_CLASSES['bar']['exactory-problem-review-v2']['field']
        size = rule['characters']
        # Native serialized strings include four framing quote characters.
        overhead = capacity.measure_capacity_value(self.route, '')
        packet = dict(protocol='exactory-problem-review-v2', field='x' * (size - overhead))
        within = self.fields.classify_packet(self.route, 'bar', packet)
        self.assertEqual(within['frame']['field'], packet['field'])
        self.assertEqual(self.fields.list_frame_units(self.route, 'bar', packet, {}), [])
        packet['field'] += 'x'
        above = self.fields.classify_packet(self.route, 'bar', packet)
        row = next(row for row in above['fields'] if row['path'] == ('field',))
        self.assertEqual(row['characters'], size + 1)
        self.assertEqual(row['travel'], 'unit')
        self.assertEqual(above['frame']['field']['sha256'], digest(packet['field']))
        self.assertEqual(above['frame']['field']['characters'], size + 1)
        units = self.fields.list_frame_units(self.route, 'bar', packet, {})
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['sha256'], digest(packet['field']))
        declared = self.fields.describe_role_frame(self.route, 'bar', packet, ('request',))['request']
        self.assertLessEqual(capacity.measure_capacity_value(self.route, above['frame']), sum(declared.values()))

    def test_every_role_form_has_a_frame_at_most_two_hundred_thousand(self):
        for role, forms in self.fields.FIELD_CLASSES.items():
            for protocol, table in forms.items():
                packet = {key: None for key in table if '.' not in key}
                if 'protocol' in packet:
                    packet['protocol'] = protocol
                if protocol == 'native':
                    packet.pop('protocol', None)
                elif protocol in ('readiness', 'round'):
                    packet['kind'] = protocol
                frames = self.fields.describe_role_frame(self.route, role, packet, ('request', 'final'))
                for values in frames.values():
                    self.assertLessEqual(sum(values.values()), capacity.FRAME_MAXIMUM_CHARACTERS, (role, protocol))

    def test_lowered_request_and_final_frames_supply_the_same_stable_unit_rows(self):
        packets = build_problem_packets()
        for route, state in ((build_route(effort='xhigh'), 'fits'), (build_route(), 'over_limit_expected'),
                             (build_route(model='unlisted-model', output=1000), 'unmeasured')):
            profile = capacity.read_output_profile({}, route)
            for role, packet in packets.items():
                frames = self.fields.describe_role_frame(route, role, packet, ('request', 'final'))
                inventory = self.fields.list_frame_units(route, role, packet, {})
                plan = capacity.plan_chain_capacity(route, profile, frames=frames, inventory=inventory, targets=['t1'])
                self.assertEqual(plan['task']['request']['state'], state)
                for kind in ('request', 'final'):
                    frame = plan['frame'][kind]
                    units = self.fields.list_frame_units(route, role, packet, frame['bounded_fields'])
                    self.assertEqual(units, self.fields.list_frame_units(route, role, copy.deepcopy(packet), frame['bounded_fields']))
                    self.assertTrue(all(set(unit) == {'handle', 'kind', 'characters', 'sha256'} for unit in units))
                    classified = self.fields.classify_packet(route, role, packet, bounded_fields=frame['bounded_fields'])
                    self.assertLessEqual(capacity.measure_capacity_value(route, classified['frame']), frame['bound'])
                    owners = {row['path'][0] for row in classified['fields'] if row['travel'] == 'unit'}
                    for name in frame['unit_fields']:
                        if name.startswith('$'):
                            continue
                        actual = capacity.measure_capacity_value(route, packet[name])
                        if actual > frame['bounded_fields'][name]:
                            self.assertIn(name, owners)

    def test_cited_search_incident_has_no_body_left_outside_the_units(self):
        from capacity_packet_fixtures import build_scaled_record_units
        packet = build_scaled_record_units('texts', 7500000)
        classified = self.fields.classify_packet(self.route, 'result', packet)
        units = self.fields.list_frame_units(self.route, 'result', packet, {})
        bodies = [row for row in classified['fields'] if row['path'][-1] == 'content']
        self.assertTrue(bodies)
        self.assertTrue(all(row['travel'] == 'unit' and row['characters'] >= 7500000 for row in bodies))
        self.assertGreater(sum(unit['characters'] for unit in units), 7500000)
        frame = self.fields.describe_role_frame(self.route, 'result', packet, ('request',))['request']
        self.assertLessEqual(capacity.measure_capacity_value(self.route, classified['frame']), sum(frame.values()))
        self.assertEqual(classified['unit_characters'], sum(unit['characters'] for unit in units))

    def test_forming_import_incident_accounts_for_twenty_eight_million_characters(self):
        packet = build_imported_slate(28600000)
        classified = self.fields.classify_packet(self.route, 'slate', packet)
        units = self.fields.list_frame_units(self.route, 'slate', packet, {})
        self.assertTrue({digest(row) for row in packet['evidence'] + packet['problem_forming_imports']} <=
                        {row['sha256'] for row in units if row['kind'] == 'collection'})
        self.assertGreaterEqual(sum(row['characters'] for row in units), 28600000)
        frame = self.fields.describe_role_frame(self.route, 'slate', packet, ('request',))['request']
        self.assertLessEqual(capacity.measure_capacity_value(self.route, classified['frame']), sum(frame.values()))
        for row in classified['fields']:
            if 'content' in row['path'] or 'problem_forming_imports' in row['path']:
                self.assertEqual(row['travel'], 'unit')

    def test_plan_refuses_when_a_measured_range_cannot_hold_its_descriptors(self):
        from research_harness.errors import ResearchError
        from test_review_output_profile import build_row
        packet = dict(protocol='exactory-problem-review-v2', field='x' * 10000)
        profile = capacity.read_output_profile({}, self.route)
        profile['rows'] = [build_row(level=120000, kind='request', over=1)]
        with self.assertRaises(ResearchError) as caught:
            capacity.plan_chain_capacity(self.route, profile,
                frames=self.fields.describe_role_frame(self.route, 'bar', packet, ('request',)),
                inventory=self.fields.list_frame_units(self.route, 'bar', packet, {}), targets=[])
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertGreater(caught.exception.details['minimum'], caught.exception.details['room'])

    def test_a_lowered_fixed_field_keeps_its_descriptor_and_exact_unit(self):
        packet = dict(protocol='exactory-problem-review-v2', field='A stored scientific field.')
        frames = self.fields.describe_role_frame(self.route, 'bar', packet, ('request',))['request']
        bounds = dict(frames, field=0)
        classified = self.fields.classify_packet(self.route, 'bar', packet, bounded_fields=bounds)
        units = self.fields.list_frame_units(self.route, 'bar', packet, bounds)
        self.assertEqual([row['sha256'] for row in units], [digest(packet['field'])])
        self.assertEqual(units[0]['characters'], capacity.measure_capacity_value(self.route, packet['field']))
        self.assertEqual(classified['frame']['field']['sha256'], units[0]['sha256'])
        self.assertLessEqual(capacity.measure_capacity_value(self.route, classified['frame']), sum(bounds.values()))

    def build_result_frame_packet(self):
        return dict(protocol='exactory-independent-review-sources-v2', role='result', reviewer_id='reviewer',
                    assessor=dict(id='assessor'), evidence=[],
                    direction_evidence=dict(claim='x' * 15000), dossier=dict(objective='x' * 15000),
                    intent=dict(full_objective='x' * 15000), readiness=dict(inputs=dict(candidate='x' * 15000)),
                    route_capacity=dict(rules='x' * 3000), support_contract=dict(checks='x' * 3000))

    def build_lowering_profile(self):
        from test_review_output_profile import build_row
        profile = capacity.read_output_profile({}, self.route)
        profile['rows'] = [build_row(level=240000, kind=kind, over=1) for kind in ('request', 'final')]
        return profile

    def test_moved_field_declarations_reserve_the_complete_named_descriptors(self):
        packet = self.build_result_frame_packet()
        moved = ('readiness', 'support_contract')
        frames = self.fields.describe_role_frame(self.route, 'result', packet, ('request',), unit_fields=moved)
        bounded = json.loads(json.dumps(frames['request'], sort_keys=True))
        classified = self.fields.classify_packet(self.route, 'result', packet, bounded_fields=bounded)
        units = {unit['handle']: unit for unit in classified['units']}
        for name in moved:
            descriptor = classified['frame'][name]
            self.assertEqual(set(descriptor), {'handle', 'sha256', 'characters'})
            self.assertEqual(frames['request'][name], capacity.measure_capacity_value(self.route, {name: descriptor}))
            self.assertEqual(units[descriptor['handle']]['sha256'], digest(packet[name]))
        plan = capacity.plan_chain_capacity(self.route, self.build_lowering_profile(), frames={
            'request': bounded}, inventory=classified['units'], targets=[])
        for name in moved:
            self.assertEqual(plan['frame']['request']['bounded_fields'][name], frames['request'][name])
            self.assertNotIn(name, plan['frame']['request']['unit_fields'])

    def test_result_fixed_point_moves_nominal_zero_fields_and_rebuilds_stored_frames(self):
        packet = self.build_result_frame_packet()
        before = digest(packet)
        kinds = ('request', 'final')
        profile = self.build_lowering_profile()
        initial = capacity.plan_chain_capacity(self.route, profile,
            frames=self.fields.describe_role_frame(self.route, 'result', packet, kinds),
            inventory=self.fields.list_frame_units(self.route, 'result', packet, {}), targets=['t1'])
        nominal = initial['frame']['request']
        self.assertGreaterEqual(len(nominal['unit_fields']), 3)
        self.assertTrue(any(nominal['bounded_fields'][name] == 0 for name in nominal['unit_fields']))
        result = self.fields.plan_role_capacity(self.route, 'result', packet, kinds, profile=profile, targets=['t1'])
        self.assertEqual(result, self.fields.plan_role_capacity(
            self.route, 'result', copy.deepcopy(packet), kinds, profile=profile, targets=['t1']))
        self.assertEqual(result, self.fields.plan_role_capacity(
            self.route, 'result', packet, tuple(reversed(kinds)), profile=profile, targets=['t1']))
        plan = json.loads(json.dumps(result['plan'], sort_keys=True))
        self.assertEqual(plan['inventory']['digest'], digest(result['units']))
        by_handle = {unit['handle']: unit for unit in result['units']}
        for kind in kinds:
            grant = plan['frame'][kind]
            self.assertEqual(grant['unit_fields'], [])
            classified = self.fields.classify_packet(self.route, 'result', packet, bounded_fields=grant['bounded_fields'])
            self.assertLessEqual(capacity.measure_capacity_value(self.route, classified['frame']), grant['bound'])
            for name in initial['frame'][kind]['unit_fields']:
                if name in ('protocol', 'reviewer_id', 'role'):
                    self.assertEqual(classified['frame'][name], packet[name])
                    continue
                descriptor = classified['frame'][name]
                self.assertEqual(set(descriptor), {'handle', 'sha256', 'characters'})
                unit = by_handle[descriptor['handle']]
                self.assertEqual(unit['sha256'], digest(packet[name]))
                self.assertEqual(unit['characters'], capacity.measure_capacity_value(self.route, packet[name]))
                self.assertEqual(descriptor['sha256'], unit['sha256'])
                self.assertEqual(descriptor['characters'], unit['characters'])
                self.assertEqual(grant['bounded_fields'][name],
                                 capacity.measure_capacity_value(self.route, {name: descriptor}))
            for unit in self.fields.list_frame_units(self.route, 'result', packet, grant['bounded_fields']):
                self.assertEqual(by_handle[unit['handle']], unit)
        self.assertEqual(digest(packet), before)

    def test_result_fixed_point_refuses_one_character_below_the_descriptor_floor(self):
        from research_harness.errors import ResearchError
        from research_harness.review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
        packet = self.build_result_frame_packet()
        fixed = [name for name in packet if self.fields.FIELD_CLASSES['result'][packet['protocol']][name]['class'] == 'fixed']
        frames = self.fields.describe_role_frame(self.route, 'result', packet, ('request',), unit_fields=fixed)
        minimum = sum(frames['request'].values())
        profile = capacity.read_output_profile({}, self.route)
        tokens = capacity.describe_route_capacity(self.route, profile)['input_tokens']['value']
        carried = capacity.NOTES_MAXIMUM_CHARACTERS + capacity.measure_capacity_value(self.route, [])
        floor = capacity.describe_material_levels('request')[-1] + BOUNDED_NOTICE_CHARACTERS + carried
        for difference in (-1, 0):
            room = floor + minimum + difference
            measure = dict(rule='calibrated', characters_per_token=(room + 0.5) / tokens)
            if difference < 0:
                with self.assertRaises(ResearchError) as caught:
                    self.fields.plan_role_capacity(self.route, 'result', packet, ('request',),
                                                   profile=profile, measure=measure)
                self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
                self.assertEqual(caught.exception.details['minimum'], floor + minimum)
                self.assertEqual(caught.exception.details['room'], room)
            else:
                result = self.fields.plan_role_capacity(self.route, 'result', packet, ('request',),
                                                        profile=profile, measure=measure)
                self.assertEqual(result['plan']['frame']['request']['bound'], minimum)
                self.assertEqual(result['plan']['task']['request']['carried_characters'], carried)
                frame = self.fields.classify_packet(self.route, 'result', packet,
                    bounded_fields=result['plan']['frame']['request']['bounded_fields'])['frame']
                self.assertLessEqual(capacity.measure_capacity_value(self.route, frame), minimum)

    def test_role_fixed_point_uses_the_route_profile_when_none_is_supplied(self):
        packet = dict(protocol='exactory-problem-review-v2', role='result')
        profile = capacity.read_output_profile({}, self.route)
        result = self.fields.plan_role_capacity(self.route, 'result', packet, ('request',))
        self.assertEqual(result, self.fields.plan_role_capacity(
            self.route, 'result', packet, ('request',), profile=profile))
        self.assertEqual(result['units'], [])

    def test_invalid_frame_unit_tags_refuse_once_at_the_assignment_edge(self):
        from research_harness.errors import ResearchError
        for frame in ({'$unit:missing': 0}, {'$unit:value': 1, 'value': 200},
                      {'$unit:$descriptors': 0, '$descriptors': 200},
                      {'$unit:$unit:value': 0, '$unit:value': 0, 'value': 200},
                      {'$unit:': 0, '': 200}):
            with self.subTest(frame=frame), self.assertRaises(ResearchError) as caught:
                capacity.prepare_assignment_capacity({}, None, self.route,
                    dict(kind='request', frames=dict(request=frame), inventory=[], targets=[]))
            self.assertEqual(caught.exception.code, 'invalid_input')
        from research_harness.artifacts import ArtifactStore
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            declaration = dict(kind='request', frames=dict(request={'$unit:value': 0, 'value': 200}),
                               inventory=[], targets=[])
            members = capacity.prepare_assignment_capacity({}, artifacts, self.route, declaration)
            plan = capacity.read_chain_capacity_plan(artifacts, members)
            self.assertEqual(plan['frame']['request']['bounded_fields'], {'$unit:value': 0, 'value': 200})

    def test_parts_descriptor_aliases_are_collection_units_in_each_packet_form(self):
        packet = dict(protocol='exactory-independent-review-v1', review_parts=dict(units=[dict(
            handle='u1', bodies=[dict(sha256='a' * 64, aliases=[dict(path='body.txt', sha256='a' * 64)])])]))
        classified = self.fields.classify_packet(self.route, 'result', packet)
        path = ('review_parts', 'units', 0, 'bodies', 0, 'aliases')
        row = next(row for row in classified['fields'] if row['path'] == path)
        self.assertEqual(row['row']['class'], 'collection')
        self.assertEqual(row['travel'], 'unit')
        self.assertIn(digest(packet['review_parts']['units'][0]['bodies'][0]['aliases'][0]),
                      [unit['sha256'] for unit in classified['units']])
        for role, forms in self.fields.FIELD_CLASSES.items():
            for form, table in forms.items():
                if 'review_parts' in table:
                    self.assertEqual(table['review_parts.units.*.bodies.*.aliases']['class'], 'collection')

    def test_native_minimum_gaps_and_forecast_notices_have_unit_ownership(self):
        from capacity_packet_fixtures import build_scaled_stage2_native_records
        for quantity in ('sources', 'gaps', 'notices'):
            packet = build_scaled_stage2_native_records(quantity, 1)
            for form in (packet, packet['native']):
                classified = self.fields.classify_packet(self.route, 'native_math', form)
                prefix = ('native', 'scientific_context') if form is packet else ('scientific_context',)
                for suffix in (('openness_minimum',), ('minimum_gaps',), ('audit_forecast', 'notices')):
                    row = next(row for row in classified['fields'] if row['path'] == prefix + suffix)
                    self.assertEqual(row['travel'], 'unit', suffix)
                    self.assertTrue(row['row'].get('unit_rule') or row['row'].get('overflow_unit'))

    def test_nested_body_aliases_keep_their_positions_in_the_frozen_descriptor(self):
        descriptor = dict(handle='u1', occurrences=[dict(index=0)], bodies=[
            dict(sha256='b' * 64, aliases=[dict(path='one')]),
            dict(sha256='c' * 64, aliases=[dict(path='two')])])
        result = self.fields.describe_unit_references(descriptor, 5000)
        self.assertEqual(result['count'], 3)
        aliases = [row for row in result['rows'] if row['field'] == 'aliases']
        self.assertEqual([row['path'] for row in aliases], [('bodies', 0, 'aliases'), ('bodies', 1, 'aliases')])
        self.assertEqual([row['row']['path'] for row in aliases], ['one', 'two'])

    def test_reference_rows_keep_one_ordered_prefix_and_every_remainder_once(self):
        bound = self.fields.REFERENCE_ROWS_MAXIMUM_CHARACTERS
        for field in ('occurrences', 'citations', 'aliases'):
            for count in (1, 1000, 100000):
                rows = [dict(index=index, path=['items']) for index in range(count)]
                descriptor = dict(handle='u1', characters=11, sha256='a' * 64, **{field: rows})
                before = digest(descriptor)
                result = self.fields.describe_unit_references(descriptor, bound)
                self.assertEqual(result['count'], count)
                self.assertLessEqual(result['characters'], bound)
                selected = [entry['row'] for entry in result['rows']]
                remaining = [entry['row'] for entry in result['remaining']]
                self.assertEqual(selected + remaining, rows)
                self.assertTrue(all(entry['field'] == field for entry in result['rows'] + result['remaining']))
                self.assertEqual(digest(descriptor), before)
                if count == 1:
                    self.assertEqual(remaining, [])
                else:
                    self.assertTrue(remaining)

    def test_reference_prefix_fits_native_serialization_including_escaped_quotes(self):
        descriptor = dict(handle='u1', occurrences=[dict(path=['items'], index=index) for index in range(1000)])
        result = self.fields.describe_unit_references(descriptor, 5000)
        visible = dict(count=result['count'], rows=result['rows'])
        self.assertLessEqual(capacity.measure_capacity_value(self.route, visible), 5000)

    def test_reference_rows_preserve_frozen_field_order_and_stop_at_first_oversize(self):
        descriptor = dict(handle='u1', citations=[{'id': 'first'}], occurrences=[{'path': 'x' * 6000}], aliases=[{'id': 'last'}])
        result = self.fields.describe_unit_references(descriptor, 5000)
        self.assertEqual([row['field'] for row in result['rows']], ['citations'])
        self.assertEqual([row['field'] for row in result['remaining']], ['occurrences', 'aliases'])
        self.assertEqual(result['count'], 3)

    def test_registry_metadata_members_all_have_a_table_bound_or_unit_rule(self):
        from ci.capacity_packet_inventory import read_packet_registry
        registry = read_packet_registry()
        actual = self.fields.describe_registry_classes()
        for gap in ('A1_frame_metadata', 'A6_parts_frame_metadata', 'Q10_frame_metadata'):
            expected = {path for path, row in registry['quantity_contracts'].items() if row.get('gap') == gap}
            self.assertEqual(set(actual[gap]), expected)
            self.assertTrue(all(row['class'] in ('fixed', 'collection', 'body', 'carried', 'reference') for row in actual[gap].values()))
            self.assertTrue(all(row.get('characters') or row.get('unit_rule') for row in actual[gap].values()))

    def test_builder_packets_keep_the_base_fixture_digests_after_classification(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/review_field_class_packets.json').read_text())
        packets = build_problem_packets()
        for role, packet in packets.items():
            self.assertEqual(digest(packet), expected[role]['value_sha256'])
            self.assertEqual(build_stored_packet_digest(packet), expected[role]['packet_sha256'])
            self.fields.classify_packet(self.route, role, packet)
            self.fields.list_frame_units(self.route, role, packet, {})
            self.assertEqual(digest(packet), expected[role]['value_sha256'])
            self.assertEqual(build_stored_packet_digest(packet), expected[role]['packet_sha256'])


if __name__ == '__main__':
    unittest.main()
