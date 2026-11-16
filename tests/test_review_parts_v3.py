"""Parts v3 uses actual rendered delivery charges and authenticated group cards."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import review_parts as legacy, review_parts_v3 as parts
from research_harness import review_route_capacity as capacity, review_protocol as protocol
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from review_parts_fixtures import make_packet as make_legacy_packet
from test_review_route_capacity import build_route
from review_parts_fixtures import PartsResultCase


def make_packet(*args, **kwargs):
    packet = make_legacy_packet(*args, **kwargs)
    packet["protocol"] = protocol._PROBLEM_PROTOCOL
    return packet


class InventoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.route = build_route()

    def test_inventory_counts_the_descriptor_and_every_body_character(self):
        self.assertTrue(hasattr(parts, 'build_series_inventory'))
        packet = make_packet(10, 200)
        inventory = parts.build_series_inventory(self.route, packet, 'problem_result')
        self.assertEqual(len(inventory['units']), 10)
        for row in inventory['units']:
            self.assertGreaterEqual(row['characters'], 264)
            self.assertEqual(len(row['digest']), 64)
            self.assertLessEqual(capacity.measure_capacity_value(self.route, row['descriptor']),
                                 parts.UNIT_ROW_CHARACTERS)
        self.assertEqual(inventory['characters'], sum(row['characters'] for row in inventory['units']))

    def test_delivery_value_measure_uses_only_route_serialization(self):
        from research_harness import review_transport as transport
        for adapter in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1'):
            route = build_route(adapter)
            for value in ('', '"\\\nあ', dict(handle='u3', text='A quoted "value".'), [1, 2, 'λ']):
                request = transport._build_local_request(route, '', dict(carried=value), [])
                empty = transport._build_local_request(route, '', dict(carried=None), [])
                expected = (capacity.measure_request(route, request)['text_characters'] -
                            capacity.measure_request(route, empty)['text_characters'] + 4)
                with patch.object(capacity, 'describe_route_capacity', side_effect=AssertionError('Value serialization needs no priors.')):
                    self.assertEqual(capacity.measure_capacity_value(route, value), expected)

    def test_reference_overflow_retains_complete_inventory_lists(self):
        self.assertTrue(hasattr(parts, 'build_series_inventory'))
        packet = make_packet(1, 11)
        unit = legacy.enumerate_units(packet, 'problem_result')[0]
        unit['occurrences'] = [dict(path=['problem_evidence', 'openness_searches'], index=i)
                               for i in range(1000)]
        with patch.object(legacy, 'enumerate_units', return_value=[unit]):
            inventory = parts.build_series_inventory(self.route, packet, 'problem_result')
        owner = inventory['units'][0]
        self.assertEqual(len(owner['occurrences']), 1000)
        references = owner['descriptor']['references']['rows'][:]
        for row in inventory['units'][1:]:
            self.assertEqual(row['kind'], 'reference')
            self.assertLessEqual(capacity.measure_capacity_value(self.route, row['descriptor']),
                                 parts.UNIT_ROW_CHARACTERS)
            references.extend(row['descriptor']['references']['rows'])
        self.assertEqual([row['index'] for row in references], list(range(1000)))

    def test_late_duplicate_occurrences_append_reference_units(self):
        packet = make_packet(1, 11)
        before = parts.build_series_inventory(self.route, packet, 'problem_result')
        packet['problem_evidence']['openness_searches'].append(copy.deepcopy(packet['problem_evidence']['openness_searches'][0]))
        after = parts.build_series_inventory(self.route, packet, 'problem_result', previous=before)
        self.assertEqual(len(after['units'][0]['occurrences']), 2)
        self.assertEqual(after['units'][0]['text'], before['units'][0]['text'])
        self.assertEqual(after['units'][0]['characters'], before['units'][0]['characters'])
        extra = after['units'][len(before['units']):]
        self.assertTrue(extra)
        self.assertTrue(all(row['late'] and row['kind'] == 'reference' for row in extra))
        self.assertEqual(sum(row['descriptor']['references']['count'] for row in extra), 1)

    def test_real_read_packets_deliver_escaped_characters_once_within_plan(self):
        self.assertTrue(hasattr(parts, 'admit_packet'))
        packet = make_packet(80, 1200)
        packet['problem_evidence']['openness_searches'][0]['reason'] = '"\\\nあ' * 6000
        admitted = parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result')
        self.assertEqual(admitted['state'], 'accept')
        plan, inventory = admitted['plan'], admitted['inventory']
        cursor, chunks, sizes = None, {}, []
        level = plan['task']['read']['material_allowance']
        while True:
            turn = parts.size_read_turn(plan, level, inventory['units'], position=cursor)
            if turn is None:
                break
            material, context = parts.deliver_read_interval(self.route, inventory, turn)
            charge = capacity.measure_capacity_value(self.route, material)
            self.assertLess(abs(charge - turn['unit_characters']), parts.UNIT_ROW_CHARACTERS)
            for row in parts.read_material_texts(inventory, turn, material, context):
                chunks.setdefault(row['unit'], []).append(row['text'])
            sizes.append(turn['unit_characters'])
            cursor = turn['next']
        self.assertLessEqual(len(sizes), math.ceil(inventory['characters'] / level))
        for row in inventory['units']:
            self.assertEqual(''.join(chunks.get(row['handle'], [])), row['text'])


    def test_one_oversized_reference_row_is_text_and_keeps_its_digest(self):
        packet = make_packet(1, 11)
        unit = legacy.enumerate_units(packet, 'problem_result')[0]
        citation = dict(kind='record', record_kind='literature_search', id='long-' + 'x' * 30000, digest='a' * 64)
        unit['citations'] = [citation]
        with patch.object(legacy, 'enumerate_units', return_value=[unit]):
            admission = parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result')
        self.assertEqual(admission['state'], 'accept', admission)
        inventory, plan = admission['inventory'], admission['plan']
        reference = next(row for row in inventory['units'] if row['kind'] == 'reference')
        self.assertEqual(reference['digest'], digest(json.loads(reference['text'])))
        chunks, position = [], None
        while True:
            turn = parts.size_read_turn(plan, plan['task']['read']['material_allowance'], inventory['units'], position=position)
            if turn is None: break
            material, context = parts.deliver_read_interval(self.route, inventory, turn)
            chunks.extend(fragment['text'] for fragment in parts.read_material_texts(inventory, turn, material, context)
                          if fragment['unit'] == reference['handle'])
            position = turn['next']
        self.assertEqual(''.join(chunks), reference['text'])
        self.assertLessEqual(capacity.measure_capacity_value(self.route, reference['descriptor']), parts.UNIT_ROW_CHARACTERS)

    def test_binary_body_coverage_and_oversized_binary_admission(self):
        import base64
        for size in (200, 1000000):
            packet = make_packet(1, 11)
            data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
            artifact = self.artifacts.put(data, 'application/octet-stream')
            packet['problem_evidence']['assessment_evidence'] = [artifact]
            packet['evidence'] = [dict(artifact=artifact, encoding='base64', content=base64.b64encode(data).decode('ascii'))]
            if size > 15000:
                with self.assertRaises(ResearchError) as refused:
                    parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result')
                self.assertEqual(refused.exception.code, 'review_parts_inventory_too_large')
                continue
            admission = parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result')
            self.assertEqual(admission['state'], 'accept', admission)
            inventory, plan = admission['inventory'], admission['plan']
            fragments, position = {}, None
            while True:
                turn = parts.size_read_turn(plan, plan['task']['read']['material_allowance'], inventory['units'], position=position)
                if turn is None: break
                material, context = parts.deliver_read_interval(self.route, inventory, turn)
                for fragment in parts.read_material_texts(inventory, turn, material, context):
                    fragments.setdefault(fragment['unit'], []).append(fragment)
                position = turn['next']
            entries = list(parts._list_binary_entries(inventory, fragments))
            self.assertEqual([legacy._read_entry_bytes(entry) for entry in entries], [data])


    def test_requested_reference_row_is_delivered_even_with_no_body(self):
        packet = make_packet(1, 11)
        unit = legacy.enumerate_units(packet, 'problem_result')[0]
        unit['occurrences'] = [dict(path=['problem_evidence', 'openness_searches'], index=index) for index in range(1000)]
        with patch.object(legacy, 'enumerate_units', return_value=[unit]):
            inventory = parts.build_series_inventory(self.route, packet, 'problem_result')
        row = next(row for row in inventory['units'] if row['kind'] == 'reference')
        scopes = parts._resolve_requests(inventory, dict(unit_requests=[dict(unit=row['handle'])], source_requests=[]))
        state = parts.build_series_state([])
        state['pending_requests'] = copy.deepcopy(scopes)
        parts._advance_requested_ranges(state, {legacy.KEY: dict(kind='final')}, inventory)
        self.assertEqual(state['pending_requests'], scopes)
        deliveries = parts._deliver_requested_units(self.route, inventory, scopes, 15000)
        self.assertEqual(deliveries[0]['descriptor'], row['descriptor'])
        parts._advance_requested_ranges(state, {legacy.KEY: dict(kind='final'), 'review_parts_requests': deliveries}, inventory)
        self.assertEqual(state['pending_requests'], [])


class AdapterJourneyTests(unittest.TestCase):
    def setUp(self):
        original = capacity.plan_chain_capacity
        def build_request_plan(*args, **kwargs):
            kwargs['final_pages'] = False
            return original(*args, **kwargs)
        pinned = patch.object(capacity, 'plan_chain_capacity', side_effect=build_request_plan)
        pinned.start()
        self.addCleanup(pinned.stop)
        from test_review_output_profile import build_row
        for patcher in (
            patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL)),
            patch.object(capacity, 'read_profile_priors', return_value=[
                dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                for kind in ('read', 'combine', 'check', 'final')])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_native_adapter_recorded_final_authenticates_without_inline_handles(self):
        import test_review_parts_native as fixtures
        from codex_route_fixtures import invoke_codex
        case = fixtures.NativePartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        with patch.object(fixtures, 'make_output', side_effect=make_turn_output):
            turns, final = case.finish_series()
        self.assertEqual(case.read_packet(final)[legacy.KEY]['protocol'], parts.PROTOCOL)
        state = parts._read_value(case.artifacts, case.read_packet(final)[legacy.KEY]['state'])
        invoke_codex(case, final['id'], case.final_output([dict(ref='u1'), dict(ref=state['cards'][0]['id'])]))
        result = legacy.verify_series(case.store.snapshot()['records'], case.artifacts, final['id'])
        self.assertTrue(result['complete'], result['issues'])

    def test_adjudicator_adapter_records_its_disposition(self):
        import test_review_parts_adjudication_journey as fixtures
        from research_harness import review_transport as transport
        case = fixtures.AdjudicationPartsCase()
        case.output_tokens = 128000
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized(count=4, paragraphs=30000)
        from test_review_output_profile import build_row
        priors = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                  for kind in ('read', 'combine', 'check', 'final')]
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000), \
                patch.object(capacity, 'read_profile_priors', return_value=priors), \
                patch.object(case, 'output', side_effect=lambda assignment: make_turn_output(case.packet(assignment))):
            turns, final = case.finish_series()
            self.assertEqual(case.packet(final)[legacy.KEY]['protocol'], parts.PROTOCOL)
            try:
                case.record_final(final)
            except ResearchError as error:
                self.fail(repr(error.as_dict()))
        result = legacy.verify_series(case.case.read_records(), case.case.artifacts, final['id'])
        self.assertTrue(result['complete'], result['issues'])

    def test_verification_adapter_reaches_the_verdict_command_boundary(self):
        import test_review_parts_study as fixtures
        from research_harness import review_transport as transport, verification
        case = fixtures.VerificationPartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.enlarge_verification()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000), \
                patch.object(fixtures, 'make_output', side_effect=make_turn_output):
            turns, final = case.finish()
            self.assertEqual(case.packet(final)[legacy.KEY]['protocol'], parts.PROTOCOL)
            output = case.final_output(final)
            inventory = parts._read_value(case.case.artifacts, case.packet(final)[legacy.KEY]['inventory'])
            linked = next(row for row in inventory['units'] if case.case.linked in row['citations'])
            for check in output['checks']:
                check['evidence'] = [dict(ref=linked['handle'])]
            case.invoke(final, output)
            _, output = protocol.read_assignment_output(case.records(), case.case.artifacts, final['id'])
            self.assertTrue(verification.require_verdict_assignment(case.records(), case.case.artifacts,
                case.bind_value(final, output), output['verdict'])['ready'])
            payload = case.case.build_bind_payload(case.task['digest'], output['verdict'], observed=False)
            payload['assessment'].update(assignment_id=final['id'], checks=output['checks'])
            saved = case.case.mutate(verification.bind_verdict, payload)['result']
            self.assertEqual(saved['assessment']['assignment_id'], final['id'])
        self.assertTrue(legacy.verify_series(case.records(), case.case.artifacts, final['id'])['complete'])

    def test_readiness_adapter_reaches_its_review_command_boundary(self):
        import test_review_parts_study as fixtures
        from research_harness import review_transport as transport, development
        case = fixtures.ReadinessPartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.enlarge_readiness()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000), \
                patch.object(fixtures, 'make_output', side_effect=make_turn_output):
            turns, final = case.finish()
            self.assertEqual(case.packet(final)[legacy.KEY]['protocol'], parts.PROTOCOL)
            output = case.final_output(final, all_citations=True)
            case.invoke(final, output)
            verified = legacy.verify_series(case.records(), case.case.artifacts, final['id'])
            self.assertTrue(verified['complete'], verified['issues'])
            self.assertTrue(development._review(development._Context(case.records(), case.case.artifacts), output['support'], case.candidate,
                                               assignment_id=final['id'])['ready'])
            case.case.mutate(development.record_readiness_review, output['support'])
            self.assertEqual(case.records()['readiness_review'][output['support']['id']]['payload'], output['support'])

    def test_context_repair_history_is_unitized_under_the_stored_frame(self):
        import test_review_parts_repair_journey as fixtures
        from test_review_output_profile import build_row
        case = fixtures.ResultRepairJourneyTests()
        case.output_tokens = 128000
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized(count=2, characters=900000, limit=1300000)
        priors = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                  for kind in ('read', 'combine', 'check', 'final')]
        with patch.object(capacity, 'read_profile_priors', return_value=priors), \
                patch.object(fixtures, 'make_output', side_effect=make_turn_output):
            predecessor = case.assign()
            case.invoke(predecessor, make_turn_output(case.packet(predecessor)))
            binding = case.contaminate(predecessor)
            with patch.object(capacity, 'read_profile_priors', return_value=[]):
                repaired = case.repair_assignment(predecessor, binding)
            self.assertEqual(case.packet(repaired)[legacy.KEY]['protocol'], parts.PROTOCOL)
            self.assertEqual(case.packet(repaired)[legacy.KEY]['adapter'], 'context_repair')
            final = case.finish_repair(repaired)
            output = case.final_output()
            output['reassessment'] = dict(assignment_id=predecessor['id'], findings=[])
            case.record_final(final, output)
            verified = legacy.verify_series(case.case.read_records(), case.case.artifacts, final['id'])
            self.assertTrue(verified['complete'], verified['issues'])


    def test_native_command_accepts_and_audits_a_v3_series(self):
        import importlib.util
        import sys
        import tests as package
        harness = Path(__file__).resolve().parents[1] / 'skills/math-solver/harness'
        sys.path.insert(0, str(harness))
        original_paths = package.__path__
        package.__path__ = list(original_paths) + [str(harness / 'tests')]
        self.addCleanup(lambda: sys.path.remove(str(harness)))
        self.addCleanup(setattr, package, '__path__', original_paths)
        spec = importlib.util.spec_from_file_location('parts_v3_native_command_fixture', harness / 'tests/test_review_parts_native.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        case = module.NativePartsAcceptanceTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        import tests.openness_chain_support as support
        original_route = support.record_codex_route
        with patch.object(support, 'record_codex_route', side_effect=lambda fixture: original_route(fixture, output_tokens=128000)):
            case.build_native_fixture()
        from research_harness import review_transport as transport
        turns = []
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            assignment = case.assign_turn('turn-1')
            for number in range(40):
                turns.append(assignment['id'])
                packet = case.read_turn(assignment['id'])
                self.assertEqual(packet[legacy.KEY]['protocol'], parts.PROTOCOL)
                if packet[legacy.KEY]['kind'] == 'final':
                    state = parts._read_value(case.artifacts, packet[legacy.KEY]['state'])
                    output = copy.deepcopy(case.review)
                    output['schema_version'] = 2
                    output['findings']['openness'] = dict(outcome='unresolved',
                        statement_digest=output['claim_digest'], searched_at='2026-11-16',
                        coverage='The exact exported evidence.', reason='The series records an unresolved finding.',
                        evidence=[dict(ref='u1', start=0, end=20), dict(ref=state['cards'][0]['id'])])
                    case.invoke(assignment['id'], output)
                    case.review = protocol.assignment_state(case.store.snapshot()['records'], case.artifacts, assignment['id'])['output']
                    break
                case.invoke(assignment['id'], make_turn_output(packet))
                assignment = case.assign_turn('turn-' + str(number + 2), assignment['id'])
            else:
                self.fail('The native v3 command did not reach its final.')
        receipts = case.check_review()['reading_turns']
        self.assertEqual([row['assignment_id'] for row in receipts], turns)
        case.accept_result()
        self.assertIn('acceptance-000001', case.controller.status(full_audit=True)['acceptances'])


class BuilderScaleTests(unittest.TestCase):
    setUp = InventoryTests.setUp
    def run_inventory(self, state_name, count, body_characters, *, use_allowances=False, logical_override=None):
        route = build_route(model='unlisted-model' if state_name == 'unmeasured' else 'claude-opus-5-5',
                            effort='max' if state_name == 'over_limit_expected' else 'xhigh',
                            output=32000 if state_name == 'unmeasured' else 128000)
        profile = capacity.read_output_profile({}, route)
        from test_review_output_profile import build_row
        if state_name == 'fits':
            profile = capacity.read_output_profile({}, route, priors=[
                dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                for kind in ('read', 'combine', 'check', 'final')])
        elif state_name == 'over_limit_expected':
            row = copy.deepcopy(next(row for row in profile['rows'] if row['kind'] == 'reading' and row['task_level'] == 240000))
            row['kind'] = 'read'
            profile = capacity.read_output_profile({}, route, priors=[row])
        limit = capacity.describe_route_capacity(route, profile)['input_text_characters']['value']
        if body_characters is None:
            body_characters = math.ceil(30 * limit / count)
        logical = dict(protocol=protocol.PROTOCOL, role='verification', reviewer_id='reviewer', dossier_id=None,
            paper=dict(kind='verification', task_digest='a' * 64, sources=dict(work={
                'entry-' + str(index): dict(id='record-' + str(index), reason='x' * body_characters) for index in range(count)})), evidence=[])
        if logical_override is not None:
            logical = logical_override
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            admission = parts.admit_packet({}, self.artifacts, route, logical, 'study_verification')
        self.assertEqual(admission['state'], 'accept', admission)
        plan, inventory = admission['plan'], admission['inventory']
        self.assertEqual(sum(row['kind'] == 'record' for row in inventory['units']), count)
        plan_ref = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan), artifact=parts._put_value(self.artifacts, plan))
        inventory_ref = parts._put_value(self.artifacts, inventory)
        logical_ref = parts._put_value(self.artifacts, logical)
        inventory_digest, logical_digest = digest(inventory), digest(logical)
        series_state = parts.build_series_state([])
        chunks, references, counts = {}, [], dict(read=0, combine=0, condense=0, check=0, final=0)
        largest, request_count = 0, 0
        from research_harness.evaluation import Evaluation
        turn_records = dict(review_route=dict(route=route))
        evaluated_artifacts = Evaluation.of(turn_records, self.artifacts)
        observations_by_kind = {kind: [] for kind in legacy.KINDS}
        usage_rows = []
        while True:
            limits = {kind: capacity.derive_plan_task_limits(plan, kind, observations_by_kind[kind]) for kind in legacy.KINDS}
            binding = dict(adapter='study_verification', series_assignment_id='scale', series_key='a' * 64,
                plan_digest=plan_ref['digest'], inventory=inventory_ref, inventory_digest=inventory_digest,
                logical_packet=logical_ref, logical_packet_digest=logical_digest,
                state=parts._put_value(self.artifacts, series_state), state_digest=digest(series_state),
                _targets=admission['targets'])
            packet = parts.plan_series_turn(route, self.artifacts, logical, inventory, plan, series_state,
                                           binding=binding, limits_by_kind=limits)
            kind = packet[legacy.KEY]['kind']
            self.assertLessEqual(capacity.measure_capacity_value(route, packet[legacy.KEY]), parts.PROTOCOL_FRAME_CHARACTERS)
            counts[kind] += 1
            if packet[legacy.KEY].get('mode') == 'condense':
                counts['condense'] += 1
            prompt = legacy.build_turn_prompt('verification', {}, packet[legacy.KEY])
            request = protocol.transport.request_body(route, prompt['text'], packet, [], order=())
            measured = capacity.measure_request(route, request)['text_characters']
            largest = max(largest, measured)
            self.assertLessEqual(measured, limits[kind]['request_bound']['text_characters'])
            request_count += 1
            self.assertLess(request_count, 10000)
            if kind == 'final':
                break
            if kind == 'read':
                interval = parts._read_interval(packet)
                material = packet[parts.MATERIAL_KEY]
                charge = capacity.measure_capacity_value(route, material)
                self.assertLess(abs(charge - interval['unit_characters']), parts.UNIT_ROW_CHARACTERS)
                for fragment in parts.read_material_texts(inventory, interval, material, packet[parts.CONTEXT_KEY]):
                    chunks.setdefault(fragment['unit'], []).append(fragment['text'])
                references.extend(row for value in material if isinstance(value, dict)
                                  for row in value.get('references', {}).get('rows', []))
            output = make_turn_output(packet)
            if use_allowances and kind == 'read':
                for card in output['cards']:
                    target = card['group']['characters']
                    card['summary'] = 'x'
                    room = target - capacity.measure_capacity_value(route, card)
                    self.assertGreaterEqual(room, 0)
                    card['summary'] += 'x' * room
                    self.assertEqual(capacity.measure_capacity_value(route, card), target)
            assignment = dict(route_id='route', capacity_plan=plan_ref)
            delta = parts.validate_planned_output(packet, output, inventory, plan, series_state, route,
                                                 artifacts=evaluated_artifacts, assignment=assignment)
            observation = dict(packet=packet, output=output, delta=delta, inventory=inventory,
                receipt=dict(turn=request_count, assignment_id='scale-' + str(request_count),
                             attempt_id='attempt-' + str(request_count), output_digest=digest(output)))
            parts.advance_series_state(series_state, observation)
            usage = dict(output_tokens=plan['limits']['output_tokens']['value'] // 2,
                         material_allowance=limits[kind]['material_allowance'])
            observations_by_kind[kind].append(usage)
            usage_rows.append(dict(kind=kind, turn=request_count, **usage))
        recorded_usage = parts._read_value(self.artifacts, parts._put_value(self.artifacts, usage_rows))
        self.assertTrue(all(35 <= row['output_tokens'] * 100 / plan['limits']['output_tokens']['value'] <= 75
                            for row in recorded_usage))
        self.assertLessEqual(counts['read'], math.ceil(inventory['characters'] / plan['task']['read']['material_allowance']))
        for row in inventory['units']:
            self.assertGreaterEqual(row['characters'], len(row['text']) + 64)
            self.assertEqual(''.join(chunks.get(row['handle'], [])), row['text'])
        expected_references = sum(row['descriptor']['references']['count'] for row in inventory['units']
                                  if row['kind'] != 'reference')
        self.assertEqual(len(references), expected_references)
        items = {row['handle']: json.loads(row['text'][:row['components'][0]['end']])
                 for row in inventory['units'] if row['required'] and row['kind'] != 'reference'}
        rebuilt = list(legacy._rebuild_occurrences(items, inventory))
        self.assertEqual(len(rebuilt), sum(len(row['occurrences']) for row in inventory['units']))
        frame_occurrences = {digest(occurrence) for row in inventory['units'] if row['kind'] == 'frame'
                             for occurrence in row['occurrences']}
        for occurrence, item in rebuilt:
            original = inventory['frame_source'] if digest(occurrence) in frame_occurrences else logical
            self.assertEqual(digest(item), digest(legacy._find_collection(original, occurrence['path'])[legacy._find_address(occurrence)]))
        result = dict(state=state_name, units=count, body=body_characters,
                      delivery_characters=inventory['characters'], row_bound=parts.UNIT_ROW_CHARACTERS,
                      read_level=plan['task']['read']['material_allowance'],
                      final_level=plan['task']['final']['material_allowance'], largest_request=largest, turns=counts)
        print('parts-v3 builder scale ' + json.dumps(result, sort_keys=True), flush=True)
        return result

    def test_thirty_times_capacity_10000_units_uses_full_card_allowances(self):
        for state in ('fits', 'over_limit_expected', 'unmeasured'):
            with self.subTest(state=state):
                self.run_inventory(state, 10000, None, use_allowances=True)

    def test_10000_small_units_on_every_prior_class(self):
        for state in ('fits', 'over_limit_expected', 'unmeasured'):
            with self.subTest(state=state):
                self.run_inventory(state, 10000, 200)

    def test_100000_units_of_twenty_characters_count_their_rows(self):
        result = self.run_inventory('fits', 100000, 20)
        self.assertGreaterEqual(result['delivery_characters'], 8400000)

    def test_Q17_reference_shapes_keep_one_body_and_rebuild_every_occurrence(self):
        from research_harness import review_parts_roles
        adapter = review_parts_roles.get_adapter('study_verification')
        for field in ('occurrences', 'citations', 'aliases'):
            for count in (1, 1000, 100000):
                with self.subTest(field=field, count=count):
                    item = dict(id='record-0', reason='x' * 11)
                    originals = {'entry-' + str(index): copy.deepcopy(item)
                                 for index in range(count if field == 'occurrences' else 1)}
                    source = dict(item=item, occurrences=[dict(path=['paper', 'sources', 'work'], key=key) for key in originals])
                    citations = ['citation-' + str(index) for index in range(count)] if field == 'citations' else []
                    if field == 'aliases':
                        body = self.artifacts.put(b'x' * 11, 'text/plain')
                        source['bodies'] = [dict(artifact=body, content='x' * 11, encoding='utf-8',
                            aliases=[dict(body, path='aliases/' + str(index)) for index in range(count)])]
                    logical = dict(protocol=protocol.PROTOCOL, role='verification', reviewer_id='reviewer', dossier_id=None,
                        paper=dict(kind='verification', task_digest='a' * 64, sources=dict(work=originals)), evidence=[])
                    if field == 'aliases':
                        logical['evidence'] = [dict(artifact=body, content='x' * 11, encoding='utf-8')]
                    definition = dict(adapter, enumerate_units=lambda packet: [copy.deepcopy(source)],
                                      citations=lambda unit: copy.deepcopy(citations))
                    with patch.object(review_parts_roles, 'get_adapter', return_value=definition):
                        self.run_inventory('fits', 1, 11, logical_override=logical)



def make_turn_output(packet):
    header = packet[legacy.KEY]
    if header['kind'] == 'read':
        return dict(kind='read', turn=header['turn'], cards=[
            dict(group=copy.deepcopy(group), summary='The delivered evidence was reviewed.',
                 bearing=[], statements=[], depends_on='', questions='')
            for group in packet[parts.GROUPS_KEY]], unborne=[
                dict(target=row['id'], reason='No material bearing.') for row in header['targets']])
    if header['kind'] == 'combine' and header.get('mode') == 'condense':
        return dict(block=header['block']['handle'], summary='The entries retain their evidence.',
                    refs=[next(iter(packet[legacy.STATE_KEY]['cards']))['group']['handle']] if 'group' in packet[legacy.STATE_KEY]['cards'][0] else [packet[legacy.STATE_KEY]['cards'][0].get('id', packet[legacy.STATE_KEY]['cards'][0].get('block'))])
    if header['kind'] == 'combine':
        return dict(kind='combine', turn=header['turn'], combinations=[], rest='No interactions require a check.')
    return dict(kind='check', turn=header['turn'], findings=[])


class RecordedSeriesTests(PartsResultCase):
    output_tokens = 128000

    def setUp(self):
        super().setUp()
        # Keep these existing journeys on the stored request-plan shape. The
        # page module exercises prospective admission with final_pages=True.
        original = capacity.plan_chain_capacity
        def build_request_plan(*args, **kwargs):
            kwargs['final_pages'] = False
            return original(*args, **kwargs)
        pinned = patch.object(capacity, 'plan_chain_capacity', side_effect=build_request_plan)
        pinned.start()
        self.addCleanup(pinned.stop)
        patcher = patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def output(self, assignment):
        return make_turn_output(self.packet(assignment))

    def record_final(self, assignment, output=None):
        from research_harness import research_decisions
        output = output or self.final_output()
        self.invoke(assignment, output)
        _, output = protocol.read_assignment_output(self.case.read_records(), self.case.artifacts, assignment['id'])
        verified = legacy.verify_series(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        return self.case.mutate(research_decisions.record_value_review,
            dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))['result']

    def start_grouped_series(self):
        from test_review_output_profile import build_row
        profile = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('single', 'read', 'combine', 'check', 'final')])
        profile.start()
        self.addCleanup(profile.stop)
        self.oversized(count=3, characters=220000, limit=1300000)
        first = self.assign()
        self.assertGreater(len(self.packet(first)[parts.GROUPS_KEY]), 1)
        return first

    def test_group_missing_duplicate_or_changed_boundaries_is_refused(self):
        first = self.start_grouped_series()
        packet = self.packet(first)
        records = self.case.read_records()
        for mutate in (lambda output: output['cards'].pop(),
                       lambda output: output['cards'].append(copy.deepcopy(output['cards'][0])),
                       lambda output: output['cards'][0]['group']['first'].update(unit='u999'),
                       lambda output: output['cards'][0]['group']['last'].update(offset=0),
                       lambda output: output['cards'][0]['group'].update(unit_count=999)):
            output = make_turn_output(packet)
            mutate(output)
            with self.assertRaises(ResearchError) as caught:
                parts.validate_turn_output(records, self.case.artifacts, first, packet, output)
            self.assertEqual(caught.exception.code, 'invalid_value_review')
        _, final = self.finish_series(first)
        self.record_final(final)
        self.assertTrue(legacy.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])

    def test_statement_of_another_group_has_no_verified_credit(self):
        first = self.start_grouped_series()
        packet = self.packet(first)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        output = make_turn_output(packet)
        other = packet[parts.GROUPS_KEY][-1]['last']['unit']
        output['cards'][0]['statements'] = [dict(unit=other, text='x' * 30, why='A quotation from another group.')]
        delta = parts.validate_turn_output(self.case.read_records(), self.case.artifacts, first, packet, output)
        self.assertEqual(delta['cards'][0]['statements'], [])
        self.assertEqual(delta['cards'][0]['unverified_statements'], 1)
        self.invoke(first, output)
        _, final = self.finish_series(self.assign('after-group-test', first['id']))
        self.record_final(final)

    def test_middle_unit_text_mutation_fails_series_verification(self):
        self.oversized(count=10, characters=2000, limit=1300000)
        # Task admission is explicit here so this regression exercises a recorded
        # read even on classes whose single request starts above this small input.
        from test_review_output_profile import build_row
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        packet = self.packet(first)
        self.assertEqual(packet[legacy.KEY]['protocol'], parts.PROTOCOL)
        changed = copy.deepcopy(packet)
        texts = [index for index, value in enumerate(changed[parts.MATERIAL_KEY]) if isinstance(value, str)]
        self.assertGreater(len(texts), 2)
        index = texts[len(texts) // 2]
        changed[parts.MATERIAL_KEY][index] = changed[parts.MATERIAL_KEY][index].replace('x', 'z', 1)
        changed['digest'] = digest({key: value for key, value in changed.items() if key != 'digest'})
        with self.assertRaises(ResearchError):
            parts.validate_turn_output(self.case.read_records(), self.case.artifacts, first,
                                       changed, make_turn_output(changed))
        _, final = self.finish_series(first)
        self.record_final(final)
        records = self.case.read_records()
        changed_assignment = records['review_assignment'][first['id']]
        changed_assignment['packet'] = parts._put_value(self.case.artifacts, changed)
        changed_assignment['packet_digest'] = changed['digest']
        self.assertFalse(legacy.verify_series(records, self.case.artifacts, final['id'])['complete'])

    def test_unknown_handle_is_a_review_shape_refusal(self):
        from research_harness.review_handles import expand_handles
        self.oversized(count=1, characters=50000, limit=1300000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        packet = self.packet(first)
        with self.assertRaises(ResearchError) as caught:
            expand_handles(packet, dict(evidence=[dict(ref='u999999')]),
                           artifacts=self.case.artifacts, assignment=first, raw=True)
        self.assertEqual(caught.exception.code, 'invalid_reference_handle')

    def test_final_unquoted_delivered_unit_and_card_handles_record_expanded(self):
        from research_harness.review_refusal import evidence_arrays
        self.oversized(count=3, characters=50000, limit=1300000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        _, final = self.finish_series(first)
        packet = self.packet(final)
        state = parts._read_value(self.case.artifacts, packet[legacy.KEY]['state'])
        from research_harness.review_handles import expand_handles
        inventory = parts._read_value(self.case.artifacts, packet[legacy.KEY]['inventory'])
        fragment = state['delivered_parts']['u3'][0]
        citation = expand_handles(packet, dict(ref='u3', part=1, start=0, end=15),
                                  artifacts=self.case.artifacts, assignment=final)
        self.assertEqual(self.case.artifacts.read(citation).decode('utf-8'),
                         inventory['units'][2]['text'][fragment['start']:fragment['start'] + 15])
        part_citation = expand_handles(packet, dict(ref='u3', part=1), artifacts=self.case.artifacts, assignment=final)
        self.assertEqual(self.case.artifacts.read(part_citation).decode('utf-8'),
                         inventory['units'][2]['text'][fragment['start']:fragment['end']])
        output = self.final_output()
        for array in evidence_arrays(output):
            array[:] = [dict(ref='u1'), dict(ref='u3', part=1, start=0, end=15), dict(ref=state['cards'][0]['id'])]
        invoked = self.invoke(final, output)
        self.assertNotIn('output_refusal', invoked, repr(invoked))
        records = self.case.read_records()
        attempt, expanded = protocol.read_assignment_output(records, self.case.artifacts, final['id'])
        self.assertIn('handle_expansion', attempt)
        self.assertGreater(attempt['handle_expansion']['references'], 1)
        self.assertNotIn('"ref":', json.dumps(expanded))
        review = self.case.mutate(__import__('research_harness.research_decisions', fromlist=['record_value_review']).record_value_review,
            dict(expanded, id='expanded-review', dossier_id=None, assignment_id=final['id']))['result']
        self.assertEqual(self.coverage(review), [])
        self.assertTrue(protocol.authenticate_attempt(self.case.read_records(), self.case.artifacts, final, attempt))

    def test_two_optional_sources_only_requested_source_is_an_obligation(self):
        from research_harness.review_refusal import evidence_arrays
        self.oversized(count=1, characters=50000, limit=1300000)
        first_source = self.case.metadata(91)
        second_source = self.case.metadata(92)
        sentence = 'A captured theorem bounds every finite graph.'
        self.case.capture(first_source, pdf_text=sentence)
        self.case.capture(second_source, pdf_text='Another captured source stays optional.')
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        _, final = self.finish_series(first)
        initial_digest = self.packet(first)[legacy.KEY]['inventory_digest']
        self.invoke(final, dict(kind='requests', unit_requests=[], source_requests=[
            dict(id='requested-source', kind='source', version_id=first_source, depth='fulltext')]))
        answered = self.assign('source-answer', final['id'])
        packet = self.packet(answered)
        self.assertEqual(packet[legacy.KEY]['inventory_digest'], initial_digest)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        optional = [row for row in inventory['units'] if not row['required']]
        self.assertTrue(any(row['source']['identity'].get('version_id') == second_source for row in optional))
        delivered = packet['review_parts_requests']
        self.assertEqual(''.join(row['text'] for row in delivered), sentence)
        from research_harness.review_handles import expand_handles
        from research_harness.source_links import span_locator
        partial = copy.deepcopy(packet)
        partial['review_parts_requests'][0].update(end=20, text=sentence[:20])
        cited = expand_handles(partial, dict(ref=delivered[0]['unit'], part=1),
                               artifacts=self.case.artifacts, assignment=answered)
        self.assertEqual(cited['link']['locator'], span_locator(sentence, 0, 20, excerpt_length=0))
        output = self.final_output()
        for array in evidence_arrays(output):
            array[:] = [dict(ref=delivered[0]['unit'], part=1, start=0, end=len(sentence))]
        self.invoke(answered, output)
        result = legacy.verify_series(self.case.read_records(), self.case.artifacts, answered['id'])
        self.assertTrue(result['complete'], result['issues'])
        records = self.case.read_records()
        missing = copy.deepcopy(packet)
        missing['review_parts_requests'] = []
        missing['digest'] = digest({key: value for key, value in missing.items() if key != 'digest'})
        records['review_assignment'][answered['id']].update(
            packet=parts._put_value(self.case.artifacts, missing), packet_digest=missing['digest'])
        self.assertFalse(legacy.verify_series(records, self.case.artifacts, answered['id'])['complete'])

    def test_multipart_material_resumes_pending_final_and_authenticates(self):
        self.oversized(count=1, characters=50000, limit=1300000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        _, final = self.finish_series(first)
        self.invoke(final, dict(stage='material', title='A finite argument', part=1,
                                text='The first part establishes the finite case.', more=True))
        second = self.assign('material-second', final['id'])
        before = self.packet(second)[legacy.KEY]['capacity']
        self.cap_attempt(second, 'material-at-cap')
        recovery = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts, second, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace', recovery)
        second = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
        after = self.packet(second)[legacy.KEY]['capacity']
        self.assertEqual(after['material_allowance'], before['material_allowance'])
        self.assertEqual(after['answer_bound'], before['answer_bound'] // 2)
        self.assertEqual(self.packet(second)['review_parts_material_request']['part'], 2)
        self.invoke(second, dict(stage='material', title='A finite argument', part=2,
                                 text='The second part closes the argument.', more=False))
        answered = self.assign('material-final', second['id'])
        from research_harness.review_handles import expand_handles
        expanded = expand_handles(self.packet(answered), dict(evidence=[dict(ref='m1')]),
                                  artifacts=self.case.artifacts, assignment=answered)
        self.assertEqual(self.case.artifacts.read(expanded['evidence'][0]),
            b'The first part establishes the finite case.The second part closes the argument.')
        from research_harness.review_refusal import evidence_arrays
        output = self.final_output()
        for array in evidence_arrays(output):
            array.append(dict(ref='m1'))
        self.record_final(answered, output)
        self.assertTrue(legacy.verify_series(self.case.read_records(), self.case.artifacts, answered['id'])['complete'])

    def test_capacity_read_replacement_uses_next_pinned_level(self):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_transport as transport, review_failure_class as failures
        first = self.start_grouped_series()
        def at_limit(route, request, credential):
            response = build_codex_response(route, request, self.output(first))
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            response['status'] = 'incomplete'
            response['incomplete_details'] = dict(reason='max_output_tokens')
            return response
        with patch.object(transport, 'send_request', side_effect=at_limit):
            protocol.invoke_assignment(self.case.store, dict(id='at-limit', assignment_id=first['id']),
                credential='test', expected_revision=self.case.store.revision, request_id='at-limit-call')
        records = self.case.read_records()
        recovery = capacity.plan_capacity_recovery(records, self.case.artifacts, first, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace', recovery)
        replacement = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
        before, after = self.packet(first)[legacy.KEY], self.packet(replacement)[legacy.KEY]
        self.assertEqual(before['plan_digest'], after['plan_digest'])
        self.assertEqual(before['series_assignment_id'], after['series_assignment_id'])
        self.assertLess(after['capacity']['material_allowance'], before['capacity']['material_allowance'])
        _, final = self.finish_series(replacement)
        self.record_final(final)
        self.assertTrue(legacy.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])

    def test_Q17_repeated_occurrences_record_a_review_and_missing_turn_fails(self):
        from test_review_output_profile import build_row
        self.oversized(count=1, characters=11, limit=1300000)
        original = copy.deepcopy(self.case.expected['openness_searches'][0])
        self.case.expected['openness_searches'] = [copy.deepcopy(original) for _ in range(1000)]
        priors = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                  for kind in ('read', 'combine', 'check', 'final')]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            turns, final = self.finish_series()
            inventory = legacy.read_inventory(self.case.artifacts, self.packet(final)[legacy.KEY]['inventory'])
            self.assertTrue(any(row['kind'] == 'reference' for row in inventory['units']))
            self.assertEqual(max(len(row['occurrences']) for row in inventory['units']), 1000)
            review = self.record_final(final)
            self.assertEqual(self.coverage(review), [])
            records = self.case.read_records()
            records['review_attempt'].pop('attempt-' + turns[0]['id'])
            self.assertFalse(legacy.verify_series(records, self.case.artifacts, final['id'])['complete'])

    def test_scientific_check_verifies_quotes_and_final_receives_contradiction(self):
        first = self.start_grouped_series()
        assignment, condensed, enlarged = first, False, False
        for number in range(20):
            packet = self.packet(assignment)
            header = packet[legacy.KEY]
            if header['kind'] == 'combine' and header.get('mode') == 'scientific':
                break
            output = make_turn_output(packet)
            if header['kind'] == 'read' and not enlarged:
                card = output['cards'][0]
                card['summary'] = 'x' * 12000
                card['depends_on'] = 'x' * 400
                card['questions'] = 'x' * 400
                card['statements'] = [dict(unit=card['group']['first']['unit'], text='x' * 3000, why='x' * 400) for _ in range(8)]
                card['bearing'] = [dict(target=target['id'], relation='context', reason='x' * 400) for target in header['targets'][:10]]
                borne = {row['target'] for row in card['bearing']}
                output['unborne'] = [row for row in output['unborne'] if row['target'] not in borne]
                self.assertGreater(capacity.measure_capacity_value(self.case.read_records()['review_route']['route'], card), card['group']['characters'])
                enlarged = True
            if header.get('mode') == 'condense':
                condensed = True
            self.invoke(assignment, output)
            assignment = self.assign('science-read-' + str(number), assignment['id'])
        self.assertTrue(condensed)
        self.assertEqual(packet[legacy.KEY]['kind'], 'combine')
        combination = dict(units=['u1'], question='Does this justify the claim?', view='It supports the claim.',
                           read=[dict(unit='u1', start=0, end=1000)])
        self.invoke(assignment, dict(kind='combine', turn=packet[legacy.KEY]['turn'],
                                     combinations=[combination], rest='No other interactions.'))
        checked = self.assign('science-check', assignment['id'])
        packet = self.packet(checked)
        identifier = packet[legacy.KEY]['check_combinations'][0]
        output = dict(kind='check', turn=packet[legacy.KEY]['turn'], findings=[dict(
            combination=identifier, finding='The inspected evidence contradicts the initial view.',
            effect='contradicts', statements=[dict(unit='u1', text='x' * 30, why='An inspected quotation.')])])
        malformed = copy.deepcopy(output)
        malformed['findings'][0]['statements'] = 'unverified text'
        with self.assertRaises(ResearchError):
            parts.validate_turn_output(self.case.read_records(), self.case.artifacts, checked, packet, malformed)
        delta = parts.validate_turn_output(self.case.read_records(), self.case.artifacts, checked, packet, output)
        self.assertEqual(len(delta['findings'][0]['statements']), 1)
        self.invoke(checked, output)
        final = self.assign('science-final', checked['id'])
        self.assertEqual(self.packet(final)[legacy.STATE_KEY]['findings'][0]['effect'], 'contradicts')
        self.record_final(final)

    def test_refused_read_delivers_less_and_refused_final_keeps_its_level(self):
        first = self.start_grouped_series()
        bad = self.output(first)
        bad['cards'].pop()
        result = self.invoke(first, bad)
        self.assertEqual(result['output_refusal']['code'], 'invalid_value_review')
        payload = dict(id='read-refusal', role=first['role'], route_id=first['route_id'],
            dossier_id=None, reviewer_id=first['reviewer_id'], author_id=first['author_id'],
            context=dict(output_refusal=dict(assignment_id=first['id'])))
        replacement = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertLess(self.packet(replacement)[legacy.KEY]['unit_characters'], self.packet(first)[legacy.KEY]['unit_characters'])
        turns, final = self.finish_series(replacement)
        result = self.invoke(final, dict(stage='result'))
        self.assertIn('output_refusal', result)
        payload.update(id='final-refusal', context=dict(final['context'], output_refusal=dict(assignment_id=final['id'])))
        answered = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(self.packet(answered)[legacy.KEY]['capacity']['material_allowance'],
                         self.packet(final)[legacy.KEY]['capacity']['material_allowance'])
        self.assertEqual(self.packet(answered)[legacy.STATE_KEY], self.packet(final)[legacy.STATE_KEY])
        self.record_final(answered)

    def test_A1_A6_A15_Q2_real_series_reaches_recorded_result(self):
        self.oversized(count=8, characters=200000, limit=1300000)
        first = self.assign()
        self.assertEqual(self.packet(first)[legacy.KEY]['protocol'], parts.PROTOCOL)
        turns, final = self.finish_series(first)
        review = self.record_final(final)
        records = self.case.read_records()
        result = legacy.verify_series(records, self.case.artifacts, final['id'])
        self.assertTrue(result['complete'], result['issues'])
        self.assertEqual(self.coverage(review), [])
        plan_ref = first['capacity_plan']
        for assignment in turns + [final]:
            self.assertEqual(assignment['capacity_plan'], plan_ref)
            self.assertEqual(self.packet(assignment)[legacy.KEY].get('entry'), self.packet(first)[legacy.KEY].get('entry'))
            packet = self.packet(assignment)
            self.assertNotIn('handle_table', packet)
            self.assertNotIn('receipts', packet[legacy.KEY])
            request = self.request(assignment)
            measured = capacity.measure_request(records['review_route']['route'], request)['text_characters']
            self.assertLessEqual(measured, packet[legacy.KEY]['capacity']['request_bound']['text_characters'])

    def append_search(self, identifier, characters, prefix=''):
        search = dict(id=identifier, queries=['bounded sequence'], reason=prefix + 'x' * characters)
        self.case.put('literature_search', identifier, search)
        self.case.expected['openness_searches'].append(dict(search=search, sources=[]))

    def test_two_late_additions_share_groups_and_keep_the_stored_plan(self):
        self.oversized(count=1, characters=45000, limit=1300000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        plan_bytes = self.case.artifacts.read(first['capacity_plan']['artifact'])
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, first)
        inventory0 = legacy.read_inventory(self.case.artifacts, self.packet(first)[legacy.KEY]['inventory'])
        self.invoke(first)
        self.append_search('addition-a', 42000)
        assignment = self.assign('with-addition-a', first['id'])
        inventory_a = legacy.read_inventory(self.case.artifacts, self.packet(assignment)[legacy.KEY]['inventory'])
        added_a = inventory_a['characters'] - inventory0['characters']
        added_b, b_added, reads, chunks = None, False, 1, {}
        for fragment in parts._list_packet_fragments(self.packet(first), inventory0):
            chunks.setdefault(fragment['unit'], []).append(fragment['text'])
        for index in range(70):
            packet = self.packet(assignment)
            inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
            self.assertEqual(self.case.artifacts.read(assignment['capacity_plan']['artifact']), plan_bytes)
            request = self.request(assignment)
            self.assertLessEqual(capacity.measure_request(self.case.read_records()['review_route']['route'], request)['text_characters'],
                                 packet[legacy.KEY]['capacity']['request_bound']['text_characters'])
            if packet[legacy.KEY]['kind'] == 'final':
                break
            if packet[legacy.KEY]['kind'] == 'read':
                reads += 1
                for fragment in parts._list_packet_fragments(packet, inventory):
                    chunks.setdefault(fragment['unit'], []).append(fragment['text'])
            self.invoke(assignment)
            if not b_added and any(group['late'] for group in packet[parts.GROUPS_KEY]):
                self.append_search('addition-b', 23000)
                b_added = True
            previous_size = inventory['characters']
            assignment = self.assign('late-turn-' + str(index), assignment['id'])
            if b_added and added_b is None:
                new_inventory = legacy.read_inventory(self.case.artifacts, self.packet(assignment)[legacy.KEY]['inventory'])
                added_b = new_inventory['characters'] - previous_size
        else:
            self.fail('Late additions did not finish.')
        self.assertTrue(b_added)
        level = plan['task']['read']['material_allowance']
        self.assertLessEqual(reads, sum(math.ceil(value / level) for value in (inventory0['characters'], added_a, added_b)))
        for row in inventory['units']:
            if row['required']:
                self.assertEqual(''.join(chunks.get(row['handle'], [])), row['text'])
        state = legacy.verify_series(self.case.read_records(), self.case.artifacts, assignment['id'])['state']
        self.assertLessEqual(state['cursor']['card_count'], plan['allocation']['largest_card_count'])
        self.assertLessEqual(state['cursor']['card_budget'], plan['allocation']['card_budget'])
        self.assertLessEqual(state['cursor']['card_reserve'], plan['allocation']['card_reserve'])
        self.record_final(assignment)

    def test_late_cards_beyond_reserve_keep_contradictions_after_condensing(self):
        from research_harness.review_refusal import evidence_arrays
        import codex_route_fixtures
        original_response = codex_route_fixtures.build_codex_response
        def keep_level(route, request, output):
            response = original_response(route, request, output)
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens'] // 2
            return response
        patcher = patch.object(codex_route_fixtures, 'build_codex_response', side_effect=keep_level)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.oversized(count=1, characters=17000, limit=1300000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = self.assign()
        self.invoke(first)
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, first)
        level = plan['task']['read']['material_allowance']
        late_reserve = plan['allocation']['late_unit_reserve']
        block_room = min(plan['allocation']['rooms']['combine'], plan['task']['combine']['material_allowance'])
        count = math.ceil((late_reserve + block_room + 15000) / plan['allocation']['entry_sizes']['card'])
        sentence = 'No finite case supports the target conclusion.'
        for index in range(count):
            self.append_search('late-evidence-' + str(index), level - 1000, sentence)
        assignment = self.assign('late-overflow', first['id'])
        contradiction, condensing, observed_reads = None, [], []
        for index in range(180):
            packet = self.packet(assignment)
            header = packet[legacy.KEY]
            if header['kind'] == 'final':
                break
            output = make_turn_output(packet)
            inventory = legacy.read_inventory(self.case.artifacts, header['inventory'])
            if header['kind'] == 'read':
                observed_reads.append(dict(cards=packet[parts.GROUPS_KEY]))
                for card in output['cards']:
                    group = card['group']
                    if group['late'] and group['late_reserve_before'] + group['entry_characters'] > late_reserve:
                        for row, start, end in parts._list_interval_units(inventory, group['first'], group['last']):
                            left, right = parts._find_text_offset(row, start), parts._find_text_offset(row, end)
                            if sentence in row['text'][left:right]:
                                target = header['targets'][0]['id']
                                card['bearing'] = [dict(target=target, relation='contradicts', reason='The delivered passage refutes the inference.')]
                                card['statements'] = [dict(unit=row['handle'], text=sentence, why='The conclusion is expressly rejected.')]
                                output['unborne'] = [value for value in output['unborne'] if value['target'] != target]
                                contradiction = dict(card=group['handle'], unit=row['handle'], text=row['text'])
                    card['summary'] = 'x'
                    remaining = group['characters'] - capacity.measure_capacity_value(self.case.read_records()['review_route']['route'], card)
                    self.assertGreaterEqual(remaining, 0)
                    card['summary'] += 'x' * remaining
            elif header.get('mode') == 'condense':
                condensing.extend(parts._read_value(self.case.artifacts, header['block_artifact'])['entries'])
            self.invoke(assignment, output)
            assignment = self.assign('overflow-turn-' + str(index), assignment['id'])
        else:
            self.fail('Late overflow did not finish.')
        self.assertIsNotNone(contradiction)
        state = parts.observe_series(self.case.read_records(), self.case.artifacts, assignment['id'])
        state = parts.build_series_state(state)
        card = next(row for row in state['cards'] if row['id'] == contradiction['card'])
        self.assertEqual(card['output']['bearing'][0]['relation'], 'contradicts')
        self.assertTrue(card['statements'])
        self.assertIn(contradiction['card'], condensing)
        self.assertNotIn(contradiction['card'], state['entries'])
        paid = sum(parts.compute_card_allowance(plan, turn['cards'])['late_unit_reserve'] for turn in observed_reads)
        beyond = sum(parts.compute_card_allowance(plan, turn['cards'])['late_characters_beyond_reserve'] for turn in observed_reads)
        self.assertEqual(state['late_unit_reserve'], paid)
        self.assertEqual(state['late_characters_beyond_reserve'], beyond)
        self.assertLessEqual(paid, late_reserve)
        self.assertGreater(beyond, block_room)
        block = next(row for row in state['combinations'] if row.get('mode') == 'condense' and contradiction['card'] in row['block']['entries'])
        start = contradiction['text'].index(sentence)
        self.invoke(assignment, dict(kind='requests', source_requests=[], unit_requests=[
            dict(unit=block['id'], entries=True), dict(unit=contradiction['card']), dict(unit=contradiction['unit'], start=start, end=start + len(sentence))]))
        answering = self.assign('archived-answer', assignment['id'])
        deliveries = self.packet(answering)['review_parts_requests']
        self.assertIn(card['output'], [json.loads(row['text']) for row in deliveries if row['unit'] == contradiction['card']])
        self.assertEqual(next(json.loads(row['text']) for row in deliveries if row['unit'] == block['id'] + '/entries')['entries'], block['block']['entries'])
        output = self.final_output()
        for array in evidence_arrays(output):
            array[:] = [dict(ref=contradiction['unit'], start=start, end=start + len(sentence)), dict(ref=contradiction['card'])]
        self.invoke(answering, output)
        attempt, expanded = protocol.read_assignment_output(self.case.read_records(), self.case.artifacts, answering['id'])
        self.case.mutate(__import__('research_harness.research_decisions', fromlist=['record_value_review']).record_value_review,
            dict(expanded, id='late-reviewed', dossier_id=None, assignment_id=answering['id']))
        self.assertTrue(legacy.verify_series(self.case.read_records(), self.case.artifacts, answering['id'])['complete'])

    def test_real_shape_7500000_cited_search_characters_have_bounded_frames(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        self.oversized(count=10, characters=750000, limit=1300000)
        first = self.assign()
        turns, final = self.finish_series(first)
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, first)
        route = self.case.read_records()['review_route']['route']
        for assignment in turns + [final]:
            packet = self.packet(assignment)
            kind = packet[legacy.KEY]['kind']
            frame = {key: value for key, value in packet.items() if key not in (
                legacy.KEY, legacy.STATE_KEY, parts.MATERIAL_KEY, parts.GROUPS_KEY, parts.CONTEXT_KEY,
                parts.SERIES_MARKER, 'review_parts_requests', 'review_parts_material_request', 'digest')}
            self.assertLessEqual(capacity.measure_capacity_value(route, frame), plan['frame'][kind]['bound'])
            self.assertEqual(validate_packet('result', packet, read_packet_registry(),
                request_limit_characters=packet[legacy.KEY]['capacity']['request_bound']['text_characters']), [])
        self.record_final(final)


    def cap_attempt(self, assignment, identifier):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_transport as transport
        def at_limit(route, request, credential):
            response = build_codex_response(route, request, {})
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            response['status'] = 'incomplete'
            response['incomplete_details'] = dict(reason='max_output_tokens')
            return response
        with patch.object(transport, 'send_request', side_effect=at_limit):
            protocol.invoke_assignment(self.case.store, dict(id=identifier, assignment_id=assignment['id']),
                credential='test', expected_revision=self.case.store.revision, request_id=identifier)

    def test_read_capacity_recovery_reaches_each_level_and_the_floor_decision(self):
        assignment = self.start_grouped_series()
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, assignment)
        levels = [row['material_allowance'] for row in plan['task']['read']['levels']]
        seen = []
        for index in range(len(levels) + 1):
            current = self.packet(assignment)[legacy.KEY]['capacity']['material_allowance']
            seen.append(current)
            self.cap_attempt(assignment, 'cap-level-' + str(index))
            recovery = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts, assignment, 'output_over_cap')
            if current == levels[-1]:
                self.assertEqual(recovery['kind'], 'decision', recovery)
                self.assertEqual(recovery['details']['handler'], 'parts_v3')
                self.assertTrue(recovery['then'])
                break
            self.assertEqual(recovery['kind'], 'replace', recovery)
            replacement = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
            self.assertEqual(replacement['route_id'], assignment['route_id'])
            assignment = replacement
        self.assertEqual(seen, levels[levels.index(seen[0]):])

    def test_combine_capacity_replacement_halves_the_block_and_replays(self):
        first = self.start_grouped_series()
        assignment = first
        for index in range(25):
            if self.packet(assignment)[legacy.KEY]['kind'] == 'combine':
                break
            self.invoke(assignment)
            assignment = self.assign('before-half-' + str(index), assignment['id'])
        else:
            self.fail('No scientific combine was assigned.')
        header = self.packet(assignment)[legacy.KEY]
        before = header.get('block', {}).get('characters', header.get('block_characters'))
        self.cap_attempt(assignment, 'combine-capped')
        recovery = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts, assignment, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace', recovery)
        replacement = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
        next_header = self.packet(replacement)[legacy.KEY]
        after = next_header.get('block', {}).get('characters', next_header.get('block_characters'))
        self.assertLessEqual(after, before // 2)
        _, final = self.finish_series(replacement)
        self.record_final(final)

    def test_a_quote_from_another_part_of_the_same_unit_gets_no_credit(self):
        from test_review_output_profile import build_row
        self.oversized(count=1, characters=640000, limit=1300000)
        search = self.case.expected['openness_searches'][0]['search']
        search['reason'] = ''.join('x' * 99000 + 'Only range number %d contains this distinct sentence.' % index for index in range(7))
        self.case.put('literature_search', search['id'], search)
        priors = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                  for kind in legacy.KINDS]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            first = self.assign()
        packet = self.packet(first)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        self.assertGreater(len(packet[parts.GROUPS_KEY]), 1)
        groups = packet[parts.GROUPS_KEY]
        found = None
        for number in range(7):
            quote = 'Only range number %d contains this distinct sentence.' % number
            owners = []
            for group in groups:
                for row, start, end in parts._list_interval_units(inventory, group['first'], group['last']):
                    if quote in row['text'][parts._find_text_offset(row, start):parts._find_text_offset(row, end)]:
                        owners.append((group['handle'], row['handle']))
            if owners and owners[0][0] != groups[0]['handle'] and owners[0][1] == groups[0]['first']['unit']:
                found = quote, owners[0][1]
                break
        self.assertIsNotNone(found)
        output = make_turn_output(packet)
        output['cards'][0]['statements'] = [dict(unit=found[1], text=found[0], why='This range belongs to another group.')]
        delta = parts.validate_turn_output(self.case.read_records(), self.case.artifacts, first, packet, output)
        self.assertEqual(delta['cards'][0]['statements'], [])
        self.assertEqual(delta['cards'][0]['unverified_statements'], 1)
        self.invoke(first, output)
        _, final = self.finish_series(self.assign('after-part-test', first['id']))
        self.record_final(final)


    def test_recorded_v2_continuation_is_identical_under_either_admission_order(self):
        from review_parts_fixtures import make_output
        self.oversized(count=8, characters=200000, limit=1300000)
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)):
            first = self.assign()
        self.assertEqual(self.packet(first)[legacy.KEY]['protocol'], legacy.PROTOCOL)
        self.invoke(first, make_output(self.packet(first)))
        logical = legacy.read_logical_packet(self.case.artifacts, self.packet(first)[legacy.KEY])
        value = dict(first, id='same-next-id', context=dict(prior_assignment_id=first['id']))
        base = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)
        outputs = []
        for order in ((legacy.PROTOCOL,), (parts.PROTOCOL, legacy.PROTOCOL)):
            with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', order):
                outputs.append(legacy.prepare_packet(self.case.read_records(), self.case.artifacts, value,
                    self.case.read_records()['review_route']['route'], logical, base, []))
        self.assertEqual(legacy.render_json(outputs[0]), legacy.render_json(outputs[1]))
        self.assertEqual(outputs[1][legacy.KEY]['protocol'], legacy.PROTOCOL)


    def test_single_attempt_at_output_limit_enters_v3_and_records_its_final(self):
        self.oversized(count=1, characters=2000, limit=1300000)
        first = self.assign()
        self.assertNotIn(legacy.KEY, self.packet(first))
        self.cap_attempt(first, 'single-at-cap')
        recovery = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts, first, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace', recovery)
        replacement = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
        self.assertEqual(self.packet(replacement)[legacy.KEY]['protocol'], parts.PROTOCOL)
        self.assertEqual(self.packet(replacement)[legacy.KEY]['replaces_assignment_id'], first['id'])
        _, final = self.finish_series(replacement)
        self.record_final(final)


class PlanProtocolTests(unittest.TestCase):
    setUp = InventoryTests.setUp
    def test_default_late_example_rebuilds_paid_and_unfunded_card_counters(self):
        from test_review_parts_v3_budget import LateCardRegressionTests
        fixture = LateCardRegressionTests()
        plan, turns, _, _ = fixture.run_appended_units(7500, [15000] * 40)
        state = parts.build_series_state([])
        for number, turn in enumerate(turns, 1):
            packet = {legacy.KEY: dict(protocol=parts.PROTOCOL, kind='read', turn=number,
                        delivery_projection=True, interval={key: value for key, value in turn.items() if key != 'cards'}),
                      parts.GROUPS_KEY: turn['cards']}
            cards = [dict(id=group['handle'], group=group, output={}, statements=[], unverified_statements=0)
                     for group in turn['cards']]
            parts.advance_series_state(state, dict(packet=packet, delta=dict(cards=cards), inventory={}, output={},
                receipt=dict(turn=number)))
        self.assertEqual((state['late_unit_reserve'], state['late_characters_beyond_reserve']), (10240, 79200))
        self.assertEqual(state['cursor']['card_budget'], 180)
        self.assertEqual(state['cursor']['card_reserve'], 2200)

    def test_final_needs_pages_is_admitted_with_the_page_allocation(self):
        from research_harness import review_parts_roles as roles
        packet = make_packet(3, 100000)
        adapter = roles.get_adapter('problem_result')
        with patch.object(roles, 'get_adapter', return_value=dict(adapter,
                targets=lambda packet: [dict(id='target-' + str(index)) for index in range(40)])):
            admission = parts.admit_packet({}, self.artifacts, build_route(output=32000), packet, 'problem_result')
        self.assertEqual(admission['state'], 'accept')
        self.assertEqual(admission['plan']['allocation']['final_form'], 'pages')
        self.assertLess(admission['plan']['task']['final']['target_allowance'], 40)

    def test_turn_header_two_and_two_hundred_only_grow_with_counters(self):
        packet = make_packet(2, 100000)
        admission = parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result')
        self.assertEqual(admission['state'], 'accept', admission)
        plan, inventory = admission['plan'], admission['inventory']
        binding = dict(adapter='problem_result', series_assignment_id='series', series_key='a' * 64,
            plan_digest=digest(plan), inventory=parts._put_value(self.artifacts, inventory), inventory_digest=digest(inventory),
            logical_packet=parts._put_value(self.artifacts, packet), logical_packet_digest=digest(packet),
            state=parts._put_value(self.artifacts, {}), state_digest='b' * 64, _targets=admission['targets'])
        limits = {kind: capacity.derive_plan_task_limits(plan, kind) for kind in legacy.KINDS}
        headers = []
        for count in (1, 199):
            state = parts.build_series_state([])
            state['turns'] = [dict(kind='read', turn=index) for index in range(count)]
            planned = parts.plan_series_turn(self.route, self.artifacts, packet, inventory, plan, state,
                                            binding=binding, limits_by_kind=limits)
            headers.append(planned[legacy.KEY])
        for header in headers:
            header.pop('turn')
            header.pop('counts')
        self.assertEqual(len(legacy.render_json(headers[0])), len(legacy.render_json(headers[1])))
        self.assertLess(capacity.measure_capacity_value(self.route, headers[1]), parts.PROTOCOL_FRAME_CHARACTERS)

    def test_three_thousand_units_share_one_complete_group_card(self):
        from research_harness import review_parts_roles as roles
        from test_review_output_profile import build_row
        packet = make_packet(0)
        adapter = roles.get_adapter('problem_result')
        sources = [dict(item=index, occurrences=[], bodies=[]) for index in range(3000)]
        with patch.object(roles, 'get_adapter', return_value=dict(adapter, enumerate_record_units=lambda packet: sources, citations=lambda unit: [])), \
                patch.object(capacity, 'read_profile_priors', return_value=[
                    dict(build_row(level=None, kind=kind, thinking=109360 if kind == 'read' else 1), largest_output_tokens=12)
                    for kind in legacy.KINDS]):
            admission = parts.admit_packet({}, self.artifacts, self.route, packet, 'problem_result')
        self.assertEqual(admission['state'], 'accept', admission)
        plan, inventory = admission['plan'], admission['inventory']
        turn = parts.size_read_turn(plan, plan['task']['read']['material_allowance'], inventory['units'])
        self.assertEqual(turn['cards'][0]['unit_count'], 3000)
        self.assertEqual(len(turn['cards']), 1)
        binding = dict(adapter='problem_result', series_assignment_id='group', series_key='a' * 64,
            plan_digest=digest(plan), inventory=parts._put_value(self.artifacts, inventory), inventory_digest=digest(inventory),
            logical_packet=parts._put_value(self.artifacts, packet), logical_packet_digest=digest(packet),
            state=parts._put_value(self.artifacts, parts.build_series_state([])), state_digest=digest(parts.build_series_state([])),
            _targets=admission['targets'])
        planned = parts.plan_series_turn(self.route, self.artifacts, packet, inventory, plan, parts.build_series_state([]),
            binding=binding, limits_by_kind={kind: capacity.derive_plan_task_limits(plan, kind) for kind in legacy.KINDS})
        output = make_turn_output(planned)
        output['cards'][0]['statements'] = [dict(unit='u' + str(index + 1), text=str(index), why='This exact value was delivered.')
                                            for index in (0, 100, 2999)]
        assignment = dict(route_id='route', capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL,
            artifact=parts._put_value(self.artifacts, plan), digest=digest(plan)))
        delta = parts.validate_turn_output(dict(review_route=dict(route=self.route)), self.artifacts, assignment, planned, output)
        self.assertEqual(len(delta['cards'][0]['statements']), 3)
        oversized = copy.deepcopy(output)
        oversized['cards'][0]['summary'] = 'x' * 12000
        with self.assertRaises(ResearchError) as refused:
            parts.validate_turn_output(dict(review_route=dict(route=self.route)), self.artifacts, assignment, planned, oversized)
        self.assertEqual(refused.exception.message, parts.READ_ANSWER_LIMIT_MESSAGE)
        oversized = copy.deepcopy(output)
        card = oversized['cards'][0]
        card['summary'] = 'x'
        room = planned[legacy.KEY]['capacity']['answer_bound'] - capacity.measure_capacity_value(self.route, card)
        card['summary'] += 'x' * room
        self.assertEqual(capacity.measure_capacity_value(self.route, card), planned[legacy.KEY]['capacity']['answer_bound'])
        with self.assertRaises(ResearchError) as refused:
            parts.validate_turn_output(dict(review_route=dict(route=self.route)), self.artifacts, assignment, planned, oversized)
        self.assertEqual(refused.exception.message, parts.READ_ANSWER_LIMIT_MESSAGE)
        oversized = copy.deepcopy(output)
        oversized['unborne'][0]['reason'] = 'x' * 401
        with self.assertRaises(ResearchError):
            parts.validate_turn_output(dict(review_route=dict(route=self.route)), self.artifacts, assignment, planned, oversized)
        output['cards'].append(copy.deepcopy(output['cards'][0]))
        with self.assertRaises(ResearchError):
            parts.validate_turn_output(dict(review_route=dict(route=self.route)), self.artifacts, assignment, planned, output)


if __name__ == '__main__':
    unittest.main()
